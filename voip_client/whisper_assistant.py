"""
Whisper-based AI Assistant bridge.

Pipeline:
1. VAD: Simple energy-based silence detection
2. STT: Whisper API for speech-to-text
3. LLM: Chat Completions API for reasoning
4. TTS: OpenAI TTS API for text-to-speech

This module provides a synchronous interface for the caller while running
the async OpenAI API calls in a background thread.
"""

import array
import asyncio
import io
import math
import os
import struct
import threading
import wave
from pathlib import Path
from queue import Queue, Empty
from typing import List, Optional

from openai import AsyncOpenAI


class WhisperAssistantBridge:
    """
    Bridge for AI Assistant using Whisper STT + Chat Completions + TTS.

    Usage (from synchronous code):

        bridge = WhisperAssistantBridge(system_message="...", voice="alloy")
        bridge.start()
        bridge.send_pcm(some_bytes)
        data = bridge.recv_pcm(timeout=0.1)
        ...
        bridge.stop()

    Audio flows:
    - send_pcm(): Incoming PCM16 from the caller, buffered until silence detected
    - recv_pcm(): Outgoing PCM16 from TTS to play back to the caller
    """

    def __init__(
        self,
        system_message: str,
        voice: str = "alloy",
        model: str = "gpt-4o",
        tts_model: str = "tts-1",
        sample_rate: int = 8000,
        silence_threshold_db: float = -40.0,
        silence_duration_ms: int = 1000,
        save_recordings: bool = False,
    ) -> None:
        self._system_message = system_message
        self._voice = voice
        # Allow overriding models via env
        env_model = os.getenv("OPENAI_MODEL", "").strip()
        self._model = env_model or model
        env_tts_model = os.getenv("OPENAI_TTS_MODEL", "").strip()
        self._tts_model = env_tts_model or tts_model
        env_voice = os.getenv("OPENAI_TTS_VOICE", "").strip()
        if env_voice:
            self._voice = env_voice
        self._sample_rate = sample_rate
        self._silence_threshold_db = silence_threshold_db
        self._silence_duration_ms = silence_duration_ms

        # Load API key
        self._openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self._openai_api_key:
            self._load_env_from_project_root()
            self._openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self._openai_api_key:
            raise ValueError("Missing OPENAI_API_KEY in environment.")

        # Conversation history
        self._messages: List[dict] = [
            {"role": "system", "content": self._system_message}
        ]

        # Queues for PCM frames
        self._input_q: "Queue[bytes]" = Queue(maxsize=200)
        self._output_q: "Queue[bytes]" = Queue(maxsize=500)

        # VAD state
        self._audio_buffer = bytearray()
        self._silence_frames = 0
        self._speech_detected = False
        # Thread / loop management
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Processing lock to avoid concurrent API calls
        self._processing = False
        
        # Recording saving
        self._save_recordings = save_recordings
        self._recordings_dir: Optional[Path] = None
        self._segment_counter = 0
        if save_recordings:
            # Will be set by parent when recordings_dir is created
            pass

    # Public API ---------------------------------------------------------

    def start(self) -> None:
        """Start background thread for processing."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="WhisperAssistantBridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal shutdown and wait for thread to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def set_sample_rate(self, sample_rate: int) -> None:
        """Update sample rate before starting processing."""
        if not sample_rate or sample_rate <= 0:
            return
        if self._thread and self._thread.is_alive():
            # Avoid changing audio params mid-stream.
            return
        self._sample_rate = int(sample_rate)
    
    def set_recordings_dir(self, recordings_dir: Path) -> None:
        """Set the directory where recordings should be saved."""
        self._recordings_dir = recordings_dir

    def send_pcm(self, data: bytes) -> None:
        """
        Enqueue a chunk of PCM16 mono audio for processing.

        Non-blocking: drops frames if the queue is full.
        """
        if not data:
            return
        try:
            self._input_q.put_nowait(data)
        except Exception:
            pass

    def recv_pcm(self, timeout: float = 0.0) -> Optional[bytes]:
        """
        Retrieve a chunk of PCM16 mono audio to play back.

        Returns None on timeout.
        """
        try:
            return self._output_q.get(timeout=timeout)
        except Empty:
            return None

    # Internal helpers ---------------------------------------------------

    @staticmethod
    def _load_env_from_project_root() -> None:
        """Best-effort load of a .env file from the project root."""
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if not env_path.exists():
            return
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key not in os.environ:
                os.environ[key] = value

    def _run_loop(self) -> None:
        """Background thread entry: create and run asyncio loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:
            print(f"[WhisperAssistantBridge] Loop error: {exc}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        """Main processing loop: read audio, detect speech, process."""
        client = AsyncOpenAI(api_key=self._openai_api_key)

        while not self._stop_event.is_set():
            # Process incoming audio frames
            try:
                chunk = self._input_q.get(timeout=0.05)
            except Empty:
                await asyncio.sleep(0.01)
                continue

            if not chunk:
                continue

            # VAD processing
            should_process = self._process_vad(chunk)

            if should_process and not self._processing:
                # Extract buffered audio and process
                audio_data = bytes(self._audio_buffer)
                self._audio_buffer.clear()
                self._speech_detected = False
                self._silence_frames = 0

                if len(audio_data) > self._sample_rate * 2 * 0.3:  # Min 300ms
                    self._processing = True
                    try:
                        await self._process_speech(client, audio_data)
                    except Exception as exc:
                        print(f"[WhisperAssistantBridge] Processing error: {exc}")
                    finally:
                        self._processing = False

    def _compute_rms(self, pcm: bytes) -> float:
        """Compute RMS (root mean square) of PCM16 audio."""
        if len(pcm) < 2:
            return 0.0
        try:
            samples = array.array("h", pcm)
        except Exception:
            return 0.0
        if not samples:
            return 0.0
        sum_sq = sum(s * s for s in samples)
        return math.sqrt(sum_sq / len(samples))

    def _is_silence(self, rms: float) -> bool:
        """Check if RMS is below silence threshold."""
        if rms <= 0:
            return True
        db = 20 * math.log10(rms / 32768.0)
        return db < self._silence_threshold_db

    def _process_vad(self, chunk: bytes) -> bool:
        """
        Process a chunk of audio for VAD.

        Returns True if we should trigger speech processing.
        """
        rms = self._compute_rms(chunk)
        is_silent = self._is_silence(rms)

        if not is_silent:
            # Speech detected
            self._speech_detected = True
            self._silence_frames = 0
            self._audio_buffer.extend(chunk)
        elif self._speech_detected:
            # Still accumulating after speech started
            self._audio_buffer.extend(chunk)
            # Count silence frames
            frame_duration_ms = (len(chunk) / 2) / self._sample_rate * 1000
            self._silence_frames += frame_duration_ms

            # Check if silence duration exceeded
            if self._silence_frames >= self._silence_duration_ms:
                return True

        return False

    async def _process_speech(
        self, client: AsyncOpenAI, audio_data: bytes
    ) -> None:
        """Process speech: Whisper -> Chat -> TTS."""
        print("[WhisperAssistantBridge] Processing speech segment...")

        # Save speech segment if recording is enabled
        if self._save_recordings and self._recordings_dir:
            try:
                segment_path = self._recordings_dir / f"segment_{self._segment_counter:03d}.wav"
                with wave.open(str(segment_path), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(self._sample_rate)
                    wav_file.writeframes(audio_data)
                self._segment_counter += 1
            except Exception as exc:
                print(f"[WhisperAssistantBridge] Failed to save segment: {exc}")

        # 1. Transcribe with Whisper
        transcript = await self._transcribe(client, audio_data)
        if not transcript or not transcript.strip():
            print("[WhisperAssistantBridge] Empty transcript, skipping")
            return

        print(f"[WhisperAssistantBridge] User: {transcript}")

        # Save user transcription if recording is enabled
        if self._save_recordings and self._recordings_dir:
            try:
                transcript_path = self._recordings_dir / f"transcript_{self._segment_counter - 1:03d}_user.txt"
                transcript_path.write_text(transcript, encoding="utf-8")
            except Exception as exc:
                print(f"[WhisperAssistantBridge] Failed to save user transcript: {exc}")

        # 2. Add to conversation history
        self._messages.append({"role": "user", "content": transcript})

        # 3. Get AI response
        response = await self._chat_completion(client)
        if not response:
            print("[WhisperAssistantBridge] Empty response, skipping")
            return

        print(f"[WhisperAssistantBridge] Assistant: {response}")
        
        # Save assistant response transcription if recording is enabled
        if self._save_recordings and self._recordings_dir:
            try:
                response_transcript_path = self._recordings_dir / f"transcript_{self._segment_counter - 1:03d}_assistant.txt"
                response_transcript_path.write_text(response, encoding="utf-8")
            except Exception as exc:
                print(f"[WhisperAssistantBridge] Failed to save assistant transcript: {exc}")
        
        self._messages.append({"role": "assistant", "content": response})

        # 4. Convert to speech and queue
        await self._text_to_speech(client, response)

    async def _transcribe(self, client: AsyncOpenAI, audio_data: bytes) -> str:
        """Transcribe audio using Whisper API."""
        # Convert PCM to WAV in memory (Whisper requires a file format)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(audio_data)

        wav_buffer.seek(0)
        wav_buffer.name = "audio.wav"  # Whisper API needs a filename

        try:
            transcription = await client.audio.transcriptions.create(
                model="whisper-1",
                file=wav_buffer,
                response_format="text",
            )
            return transcription.strip() if transcription else ""
        except Exception as exc:
            print(f"[WhisperAssistantBridge] Whisper error: {exc}")
            return ""

    async def _chat_completion(self, client: AsyncOpenAI) -> str:
        """Get AI response using Chat Completions API."""
        try:
            # Newer models (gpt-5.x+) require max_completion_tokens instead of max_tokens
            model_lower = self._model.lower()
            if model_lower.startswith("gpt-5") or model_lower.startswith("o1"):
                # Use max_completion_tokens for newer models
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=self._messages,
                    max_completion_tokens=500,
                )
            else:
                # Use max_tokens for older models (gpt-4, gpt-3.5, etc.)
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=self._messages,
                    max_tokens=500,
                )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception as exc:
            print(
                f"[WhisperAssistantBridge] Chat error (model={self._model}): {exc}\n"
                f"  Hint: Check that the model name is correct and your API key has access to it.\n"
                f"  Common models: gpt-5.2, gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo"
            )
            return ""

    async def _text_to_speech(self, client: AsyncOpenAI, text: str) -> None:
        """Convert text to speech and queue for playback."""
        try:
            # OpenAI TTS outputs at 24kHz by default
            # We request PCM and resample to our target sample rate
            response = await client.audio.speech.create(
                model=self._tts_model,
                voice=self._voice,
                input=text,
                response_format="pcm",  # Raw PCM16 at 24kHz
            )

            # Read the PCM data
            pcm_24k = response.content

            # Resample from 24kHz to target sample rate (e.g., 8kHz)
            pcm_resampled = self._resample_pcm(pcm_24k, 24000, self._sample_rate)

            # Queue in chunks for smooth playback
            chunk_size = self._sample_rate * 2 // 50  # 20ms chunks
            for i in range(0, len(pcm_resampled), chunk_size):
                chunk = pcm_resampled[i : i + chunk_size]
                try:
                    self._output_q.put_nowait(chunk)
                except Exception:
                    pass  # Drop if full

        except Exception as exc:
            print(f"[WhisperAssistantBridge] TTS error: {exc}")

    def _resample_pcm(
        self, pcm_data: bytes, src_rate: int, dst_rate: int
    ) -> bytes:
        """
        Simple linear interpolation resampling for PCM16 mono.
        
        For production, consider using a proper resampling library like scipy.signal.resample
        or soxr for better quality.
        """
        if src_rate == dst_rate:
            return pcm_data

        if not pcm_data or len(pcm_data) < 2:
            return pcm_data

        # Unpack samples
        num_samples = len(pcm_data) // 2
        if num_samples == 0:
            return pcm_data
        
        try:
            samples_tuple = struct.unpack(f"<{num_samples}h", pcm_data)
        except struct.error:
            # Invalid PCM data
            return pcm_data
        
        # Convert to list for easier manipulation
        samples = list(samples_tuple)

        # Calculate output length and ratio
        ratio = dst_rate / src_rate
        out_len = int(num_samples * ratio)
        
        if out_len == 0:
            return pcm_data

        # Linear interpolation with proper bounds checking
        out_samples = []
        for i in range(out_len):
            src_idx = i / ratio
            idx0 = int(src_idx)
            idx1 = idx0 + 1
            
            # Ensure indices are valid (handle edge case at the end)
            if idx0 >= num_samples - 1:
                idx0 = num_samples - 1
                idx1 = num_samples - 1
                frac = 0.0
            else:
                idx0 = max(0, idx0)
                idx1 = min(idx1, num_samples - 1)
                frac = src_idx - idx0

            sample = int(samples[idx0] * (1 - frac) + samples[idx1] * frac)
            sample = max(-32768, min(32767, sample))
            out_samples.append(sample)

        if not out_samples:
            return pcm_data
            
        return struct.pack(f"<{len(out_samples)}h", *out_samples)
