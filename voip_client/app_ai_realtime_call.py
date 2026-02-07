"""
AI Assistant using OpenAI Realtime API (full-duplex audio via WebSocket).

Usage:
    python -m voip_client.app_ai_realtime_call <phone_number> [--silence-duration MS]

This script:
- Registers to the SIP server using the same .env as the other app_* tools
- Places an outbound call to the given destination
- When media becomes active, attaches an OpenAI Realtime bridge so the remote
  caller can have a natural conversation with an AI assistant.

Pipeline:
- Full-duplex audio over WebSocket to OpenAI Realtime API
- Server-side VAD for turn detection
- Lower latency than the Whisper STT + Chat + TTS pipeline

Architecture:
- All conference bridge ports are C-level (AudioMediaRecorder, AudioMediaPlayer).
- A Python thread bridges audio between WAV files and the OpenAI Realtime API,
  completely outside the PJSIP media path.  This avoids the GIL deadlock
  and broken timing caused by Python AudioMediaPort on macOS ARM64.
"""

import argparse
import os
import shutil
import struct
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional, Tuple

try:
    import pjsua2 as pj
except Exception as exc:  # pragma: no cover - runtime import
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.openai_realtime import OpenAIRealtimeBridge
from voip_client.voip_common import BaseVoipCall, VoipAccount, VoipSession

# WAV header is always 44 bytes for standard PCM format.
_WAV_HEADER_SIZE = 44
# How long (seconds) to wait for more response audio before considering
# the response complete and starting playback.
_RESPONSE_DRAIN_TIMEOUT_S = 0.3


def _tmp_dir() -> Path:
    """Return (and create) a tmp/ directory under the project root."""
    d = Path(__file__).resolve().parent.parent / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    """Write raw PCM16 mono bytes to a WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def _recordings_dir() -> Path:
    """Return (and create) a recordings/ directory under the project root."""
    d = Path(__file__).resolve().parent.parent / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_wav_sample_rate(path: Path) -> int:
    """Read sample rate from WAV file header (bytes 24-27)."""
    try:
        with open(path, "rb") as f:
            f.seek(24)  # Sample rate is at offset 24-27 in WAV header
            sample_rate = struct.unpack("<I", f.read(4))[0]
            return sample_rate if sample_rate > 0 else 8000
    except Exception:
        return 8000  # fallback to default


def _resample_pcm(pcm_data: bytes, src_rate: int, dst_rate: int) -> bytes:
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
            idx1 = min(num_samples - 1, idx1)
            frac = src_idx - idx0
        
        # Interpolate
        val0 = samples[idx0]
        val1 = samples[idx1]
        interpolated = val0 + frac * (val1 - val0)
        out_samples.append(int(round(interpolated)))
    
    # Pack back to bytes
    return struct.pack(f"<{out_len}h", *out_samples)


class AiRealtimeCall(BaseVoipCall):
    """
    Outbound call whose remote party talks to an AI via OpenAI Realtime API.

    Full-duplex audio is streamed over WebSocket. Turn detection is handled
    by OpenAI's server-side VAD.
    All conference bridge ports are C-level (recorder + player).
    A background Python thread bridges audio between WAV files and OpenAI.
    """

    def __init__(
        self,
        account: VoipAccount,
        system_message: str,
        voice: str,
        model: str = "gpt-realtime",
        silence_duration_ms: int = 1000,
        vad_threshold: float = 0.5,
        prefix_padding_ms: int = 300,
        save_recordings: bool = False,
    ) -> None:
        super().__init__(account)
        self._system_message = system_message
        self._voice = voice
        self._model = model
        self._silence_duration_ms = silence_duration_ms
        self._vad_threshold = vad_threshold
        self._prefix_padding_ms = prefix_padding_ms
        self._bridge: Optional[OpenAIRealtimeBridge] = None
        self._sample_rate = 8000
        self._io_stop = threading.Event()
        self._io_thread: Optional[threading.Thread] = None
        self._recording_path: Optional[Path] = None
        self._save_recordings = save_recordings
        self._recordings_dir: Optional[Path] = None
        if save_recordings:
            call_ts = int(time.time())
            self._recordings_dir = _recordings_dir() / f"app_ai_realtime_call_{call_ts}"
            self._recordings_dir.mkdir(parents=True, exist_ok=True)
        # Queue of (pcm_bytes, response_index) produced by the IO thread.
        self._pending_responses: "Queue[Tuple[bytes, int]]" = Queue()
        # Debug log: set by run_call when --debug (file handle + lock).
        self._debug_file = None
        self._debug_lock = None

    def _debug_log(self, event: str, detail: str = "") -> None:
        """Write a timestamped event line to the debug log file if --debug was used."""
        f = getattr(self, "_debug_file", None)
        lock = getattr(self, "_debug_lock", None)
        if not f or not lock:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        line = f"{ts} {event} {detail}\n"
        try:
            with lock:
                f.write(line)
                f.flush()
        except Exception:
            pass

    def _cleanup_media(self) -> None:
        """Stop Realtime bridge and IO thread on disconnect."""
        self._debug_log("call_disconnected", "")
        self._io_stop.set()

        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                pass

        if self._io_thread is not None:
            self._io_thread.join(timeout=3)
            self._io_thread = None

        # Drop any queued, but unplayed, responses.
        try:
            while True:
                self._pending_responses.get_nowait()
        except Empty:
            pass

        # Save main recording if requested
        if self._save_recordings and self._recording_path and self._recording_path.exists():
            if self._recordings_dir:
                dest_path = self._recordings_dir / f"full_call_{int(time.time())}.wav"
                try:
                    shutil.copy2(self._recording_path, dest_path)
                    print(f"[AiRealtimeCall] Saved full call recording: {dest_path}")
                except Exception as exc:
                    print(f"[AiRealtimeCall] Failed to save recording: {exc}")

        super()._cleanup_media()

    def onCallMediaState(self, prm: Any) -> None:
        """Set up C-level recorder and start the file-based IO thread."""
        if self._media_setup_done:
            return

        ci = self.getInfo()
        for mi in ci.media:
            if mi.type != pj.PJMEDIA_TYPE_AUDIO:
                continue
            if mi.status != pj.PJSUA_CALL_MEDIA_ACTIVE:
                continue

            try:
                aud_med = self.getAudioMedia(mi.index)
            except Exception:
                continue

            mgr = self._account.ep.audDevManager()
            cap_med = mgr.getCaptureDevMedia()
            self._cap_med = cap_med
            self._aud_med = aud_med

            try:
                # Detect sample rate from the call stream.
                try:
                    port_info = aud_med.getPortInfo()
                    self._sample_rate = int(
                        getattr(port_info.format, "clockRate", 8000) or 8000
                    )
                except Exception:
                    self._sample_rate = 8000

                # --- Recorder (C-level port): captures remote audio to WAV ---
                ts = int(time.time())
                self._recording_path = _tmp_dir() / f"app_ai_realtime_call_rec_{ts}.wav"
                self._recorder = pj.AudioMediaRecorder()
                self._recorder.createRecorder(str(self._recording_path))
                aud_med.startTransmit(self._recorder)

                # Mute capture device; connection still needed to activate
                # the sound device (port 0) which drives the conference clock.
                cap_med.adjustTxLevel(0.0)
                cap_med.startTransmit(aud_med)

                # Create and start the OpenAI Realtime bridge.
                # OpenAI Realtime API requires sample_rate >= 24000 Hz.
                # We'll upsample from call rate (typically 8000 Hz) to 24000 Hz for OpenAI,
                # and downsample responses back to call rate for playback.
                openai_sample_rate = 24000
                self._bridge = OpenAIRealtimeBridge(
                    system_message=self._system_message,
                    voice=self._voice,
                    model=self._model,
                    sample_rate=openai_sample_rate,
                    silence_duration_ms=self._silence_duration_ms,
                    vad_threshold=self._vad_threshold,
                    prefix_padding_ms=self._prefix_padding_ms,
                )
                self._bridge.start()

                self._io_stop.clear()
                self._io_thread = threading.Thread(
                    target=self._audio_io_loop, daemon=True
                )
                self._io_thread.start()

                # Get the model name from the bridge for logging
                model_name = getattr(self._bridge, "_model", "unknown")
                print(
                    f"[AiRealtimeCall] Media ready: sample_rate={self._sample_rate}, "
                    f"recording={self._recording_path}, model={model_name}"
                )
                self._debug_log("media_ready", f"sample_rate={self._sample_rate} model={model_name}")
            except Exception as exc:
                print(f"[AiRealtimeCall] Failed to set up media: {exc}")
                return

            self._media_setup_done = True
            break

    # -----------------------------------------------------------------
    # Background IO thread -- runs entirely outside the PJSIP media path
    # -----------------------------------------------------------------

    def _audio_io_loop(self) -> None:
        """Read from the growing recorder WAV and feed the Realtime bridge.

        When the bridge produces response audio, accumulate it and queue
        it for playback on the main thread.  Only file I/O and OpenAI
        calls happen here; all PJSIP interaction stays on the main thread.
        """
        rec_path = self._recording_path
        if rec_path is None:
            return

        # Wait for the recorder to create the file and write the header.
        while not self._io_stop.is_set():
            if rec_path.exists() and rec_path.stat().st_size >= _WAV_HEADER_SIZE:
                break
            time.sleep(0.05)

        # Give the bridge a moment to fully initialize the WebSocket connection
        time.sleep(0.5)

        # Read the actual sample rate from the WAV file header
        actual_sample_rate = _read_wav_sample_rate(rec_path)
        file_sample_rate = actual_sample_rate
        call_sample_rate = self._sample_rate  # Call stream rate (typically 8000 Hz)
        openai_sample_rate = 24000  # OpenAI requires >= 24000 Hz
        
        needs_file_to_call_resampling = (file_sample_rate != call_sample_rate)
        needs_call_to_openai_resampling = (call_sample_rate != openai_sample_rate)
        
        if needs_file_to_call_resampling:
            print(
                f"[AiRealtimeCall] Sample rate mismatch detected: "
                f"call_stream={call_sample_rate}Hz, file={file_sample_rate}Hz, "
                f"will resample file audio to match call stream rate"
            )
        if needs_call_to_openai_resampling:
            print(
                f"[AiRealtimeCall] Upsampling audio from {call_sample_rate}Hz to {openai_sample_rate}Hz for OpenAI API"
            )

        # Bytes per frame: 20 ms of PCM16 mono at file sample rate
        # We read at file rate, resample to call rate, then upsample to OpenAI rate
        frame_bytes_file = file_sample_rate * 2 // 50  # 320 @ 8 kHz, 640 @ 16 kHz
        frame_bytes_call = call_sample_rate * 2 // 50  # Frame size at call rate
        frame_bytes_openai = openai_sample_rate * 2 // 50  # Frame size for OpenAI (960 bytes @ 24 kHz)

        try:
            f = open(rec_path, "rb")
        except OSError:
            return

        try:
            f.seek(_WAV_HEADER_SIZE)  # skip WAV header
            leftover = b""
            response_counter = 0
            
            # Accumulate response audio across multiple loop iterations
            accumulated_response = bytearray()
            last_audio_time = None

            frames_sent = 0
            drain_after_stop = False
            drain_after_stop_count = 0
            while not self._io_stop.is_set() or (drain_after_stop and drain_after_stop_count < 50):
                # Continue draining briefly after _io_stop is set to catch late-arriving audio
                if self._io_stop.is_set() and not drain_after_stop:
                    drain_after_stop = True
                    print("[AiRealtimeCall] I/O stop requested, continuing to drain pending audio...")
                
                if drain_after_stop:
                    drain_after_stop_count += 1
                
                # --- Capture: read new audio from the recording file ---
                # Skip reading input after stop is requested
                if not self._io_stop.is_set():
                    raw = f.read(frame_bytes_file * 10)  # read up to 200 ms at file rate
                else:
                    raw = b""  # Don't read more input after stop
                if raw:
                    raw = leftover + raw
                    leftover = b""
                    # Split into complete frames at file rate
                    n_full = (len(raw) // frame_bytes_file) * frame_bytes_file
                    if n_full > 0:
                        for i in range(0, n_full, frame_bytes_file):
                            frame_at_file_rate = raw[i : i + frame_bytes_file]
                            
                            # Step 1: Resample from file rate to call rate (if needed)
                            if needs_file_to_call_resampling:
                                frame_at_call_rate = _resample_pcm(
                                    frame_at_file_rate, file_sample_rate, call_sample_rate
                                )
                                # Ensure resampled frame is valid size
                                expected_call_size = call_sample_rate * 2 // 50
                                if len(frame_at_call_rate) != expected_call_size:
                                    if len(frame_at_call_rate) > expected_call_size:
                                        frame_at_call_rate = frame_at_call_rate[:expected_call_size]
                                    elif len(frame_at_call_rate) > 0:
                                        padding = expected_call_size - len(frame_at_call_rate)
                                        frame_at_call_rate += b'\x00\x00' * (padding // 2)
                            else:
                                frame_at_call_rate = frame_at_file_rate
                            
                            # Step 2: Upsample from call rate to OpenAI rate (24000 Hz)
                            if needs_call_to_openai_resampling:
                                frame_at_openai_rate = _resample_pcm(
                                    frame_at_call_rate, call_sample_rate, openai_sample_rate
                                )
                                # Ensure resampled frame is valid size
                                expected_openai_size = openai_sample_rate * 2 // 50
                                if len(frame_at_openai_rate) != expected_openai_size:
                                    if len(frame_at_openai_rate) > expected_openai_size:
                                        frame_at_openai_rate = frame_at_openai_rate[:expected_openai_size]
                                    elif len(frame_at_openai_rate) > 0:
                                        padding = expected_openai_size - len(frame_at_openai_rate)
                                        frame_at_openai_rate += b'\x00\x00' * (padding // 2)
                            else:
                                frame_at_openai_rate = frame_at_call_rate
                            
                            if self._bridge and len(frame_at_openai_rate) > 0:
                                self._bridge.send_pcm(frame_at_openai_rate)
                                frames_sent += 1
                                if frames_sent == 1:
                                    print(f"[AiRealtimeCall] Started sending audio to bridge (frame_size={len(frame_at_openai_rate)} bytes @ {openai_sample_rate}Hz)")
                    leftover = raw[n_full:]

                # --- Response: continuously drain bridge output ---
                # Check for new audio chunks (non-blocking)
                # Continue draining even after _io_stop if we're in drain_after_stop mode
                got_chunk_this_iteration = False
                drain_iterations = 0
                while (not self._io_stop.is_set() or drain_after_stop) and drain_iterations < 50:
                    chunk = self._bridge.recv_pcm(timeout=0.0)  # Non-blocking
                    if chunk:
                        accumulated_response.extend(chunk)
                        last_audio_time = time.time()
                        got_chunk_this_iteration = True
                        drain_iterations = 0  # Reset counter when we get audio
                        if len(accumulated_response) <= 24000:  # Log first ~0.5 seconds
                            print(f"[AiRealtimeCall] Accumulating audio: +{len(chunk)} bytes (total: {len(accumulated_response)} bytes)")
                    else:
                        drain_iterations += 1
                        if drain_iterations >= 5:  # Stop after 5 empty checks
                            break
                
                # If we have accumulated audio, queue it when:
                # 1. Response is done (immediate), OR
                # 2. No audio received for timeout period, OR
                # 3. I/O stop requested (flush what we have)
                current_time = time.time()
                response_complete = self._bridge._response_done.is_set() if self._bridge else False
                should_queue = False
                
                if accumulated_response:
                    if response_complete:
                        # Response is complete, queue immediately
                        should_queue = True
                    elif self._io_stop.is_set():
                        # I/O stop requested, flush what we have
                        should_queue = True
                    elif last_audio_time is None:
                        last_audio_time = current_time
                    elif current_time - last_audio_time >= _RESPONSE_DRAIN_TIMEOUT_S:
                        # No audio received for timeout period
                        should_queue = True
                    
                    if should_queue:
                        response_pcm_bytes = bytes(accumulated_response)
                        if needs_call_to_openai_resampling:
                            # Downsample from OpenAI rate back to call rate
                            response_pcm_bytes = _resample_pcm(
                                response_pcm_bytes, openai_sample_rate, call_sample_rate
                            )
                        
                        # Hand off the response PCM to the main thread for playback.
                        try:
                            self._pending_responses.put(
                                (response_pcm_bytes, response_counter), timeout=0.1
                            )
                            reason = "response complete" if response_complete else f"timeout ({current_time - last_audio_time:.2f}s)"
                            print(f"[AiRealtimeCall] Queued response {response_counter} ({len(response_pcm_bytes)} bytes @ {call_sample_rate}Hz, {reason})")
                            self._debug_log("response_queued", f"index={response_counter} bytes={len(response_pcm_bytes)} {reason}")
                            response_counter += 1
                            accumulated_response.clear()
                            last_audio_time = None
                            # Clear the response done flag for next response
                            if self._bridge:
                                self._bridge._response_done.clear()
                        except Exception as exc:
                            # Best-effort; drop response on failure.
                            print(f"[AiRealtimeCall] Failed to queue response: {exc}")
                            accumulated_response.clear()
                            last_audio_time = None
                            if self._bridge:
                                self._bridge._response_done.clear()

                # Brief sleep when there's nothing to read yet.
                if not raw and not got_chunk_this_iteration:
                    time.sleep(0.02)
        finally:
            # Drain any remaining audio from bridge before closing
            # Wait a bit longer to catch late-arriving audio after call ends
            if self._bridge:
                self._debug_log("flush_start", "")
                print("[AiRealtimeCall] Flushing remaining audio from bridge...")
                # First, drain any immediately available chunks
                drained_any = False
                for _ in range(100):  # Try up to 100 times (non-blocking)
                    chunk = self._bridge.recv_pcm(timeout=0.0)
                    if chunk:
                        accumulated_response.extend(chunk)
                        drained_any = True
                        print(f"[AiRealtimeCall] Draining final chunk: {len(chunk)} bytes (total: {len(accumulated_response)} bytes)")
                    else:
                        break
                
                # Wait for bridge to finish receiving events
                # The bridge thread might still be processing events asynchronously
                bridge_thread_running = self._bridge._thread and self._bridge._thread.is_alive()
                print(f"[AiRealtimeCall] Bridge thread running: {bridge_thread_running}, waiting for audio...")
                
                # Wait for late-arriving audio; shorten wait if no audio ever received (e.g. 603 Declined)
                max_wait_iterations = 60  # Up to 3 seconds when we might get audio
                consecutive_empty_checks = 0
                for i in range(max_wait_iterations):
                    time.sleep(0.05)
                    chunk = self._bridge.recv_pcm(timeout=0.0)
                    if chunk:
                        accumulated_response.extend(chunk)
                        drained_any = True
                        consecutive_empty_checks = 0
                        print(f"[AiRealtimeCall] Got trailing chunk: {len(chunk)} bytes (total: {len(accumulated_response)} bytes)")
                    else:
                        consecutive_empty_checks += 1
                    
                    if self._bridge._response_done.is_set() and consecutive_empty_checks >= 5:
                        print("[AiRealtimeCall] Response done, no more audio expected")
                        break
                    
                    if i >= 20 and not bridge_thread_running and consecutive_empty_checks >= 10:
                        print("[AiRealtimeCall] Bridge thread stopped, no more audio expected")
                        break
                    
                    # If call ended very quickly and we still have no audio after 1.5s, stop waiting
                    if i >= 30 and not accumulated_response:
                        print("[AiRealtimeCall] No audio received after 1.5s (call may have been declined/short), stopping flush wait")
                        break
                    
                    if i % 10 == 0:
                        bridge_thread_running = self._bridge._thread and self._bridge._thread.is_alive()
                        if bridge_thread_running:
                            print(f"[AiRealtimeCall] Still waiting for audio (iteration {i}/{max_wait_iterations})...")
                
                # Flush any accumulated audio
                if accumulated_response:
                    response_pcm_bytes = bytes(accumulated_response)
                    if needs_call_to_openai_resampling:
                        response_pcm_bytes = _resample_pcm(
                            response_pcm_bytes, openai_sample_rate, call_sample_rate
                        )
                    try:
                        self._pending_responses.put(
                            (response_pcm_bytes, response_counter), timeout=0.1
                        )
                        print(f"[AiRealtimeCall] Flushed final response {response_counter} ({len(response_pcm_bytes)} bytes @ {call_sample_rate}Hz)")
                        self._debug_log("flush_end", f"flushed_bytes={len(response_pcm_bytes)}")
                    except Exception as exc:
                        print(f"[AiRealtimeCall] Failed to flush final response: {exc}")
                else:
                    print("[AiRealtimeCall] No audio to flush")
                    self._debug_log("flush_end", "no_audio")
            f.close()

    def _play_response(self, pcm: bytes, index: int) -> None:
        """Write a response WAV and play it into the call."""
        if self._aud_med is None or self._io_stop.is_set():
            return

        wav_path = _tmp_dir() / f"app_ai_realtime_call_resp_{int(time.time())}_{index}.wav"
        _write_wav(wav_path, pcm, self._sample_rate)

        duration_s = len(pcm) / (self._sample_rate * 2)
        print(f"[AiRealtimeCall] Playing response {index} ({duration_s:.1f}s)")
        self._debug_log("response_play_start", f"index={index} duration_s={duration_s:.2f}")

        try:
            player = pj.AudioMediaPlayer()
            player.createPlayer(str(wav_path), pj.PJMEDIA_FILE_NO_LOOP)
            player.startTransmit(self._aud_med)

            # Wait for playback to finish.
            deadline = time.monotonic() + duration_s + 0.5
            while time.monotonic() < deadline and not self._io_stop.is_set():
                # Keep PJSIP responsive while we wait for playback to finish.
                time.sleep(0.05)

            try:
                player.stopTransmit(self._aud_med)
            except Exception:
                pass
            del player
        except Exception as exc:
            print(f"[AiRealtimeCall] Playback error: {exc}")

        self._debug_log("response_play_end", f"index={index}")
        # Save response file if requested, otherwise clean up
        if self._save_recordings and self._recordings_dir:
            try:
                dest_path = self._recordings_dir / f"response_{index:03d}.wav"
                shutil.copy2(wav_path, dest_path)
            except Exception as exc:
                print(f"[AiRealtimeCall] Failed to save response {index}: {exc}")
        
        # Clean up the temporary response file (always delete from tmp/)
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

    def process_pending_responses(self) -> None:
        """Play any queued AI responses (must be called on the main thread)."""
        if self._io_stop.is_set() or self._aud_med is None:
            return

        try:
            while True:
                pcm, index = self._pending_responses.get_nowait()
                self._play_response(pcm, index)
        except Empty:
            pass


def run_call(
    phone_number: str,
    reg_timeout: int,
    system_message: str,
    voice: str,
    model: str,
    silence_duration_ms: int,
    vad_threshold: float,
    prefix_padding_ms: int,
    save_recordings: bool = False,
    debug: bool = False,
) -> int:
    """Place a call and attach OpenAI Realtime bridge when media is active."""
    session = VoipSession()
    try:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            print("Missing OPENAI_API_KEY in environment or .env file.")
            return 1

        # threadCnt=0: main thread drives SIP events via libHandleEvents.
        # Real audio device (no setNullDev) provides correct conference bridge
        # timing at 50 fps.  All conference bridge ports are C-level.
        session.create_endpoint(no_vad=True)
        session.create_account()
        if not session.wait_registration(reg_timeout):
            print("Registration timed out")
            return 1

        dest_uri = session.build_uri(phone_number)
        print(f"Calling {dest_uri} (AI Realtime) ...")
        call = AiRealtimeCall(
            session.account,
            system_message=system_message,
            voice=voice,
            model=model,
            silence_duration_ms=silence_duration_ms,
            vad_threshold=vad_threshold,
            prefix_padding_ms=prefix_padding_ms,
            save_recordings=save_recordings,
        )
        call_op = pj.CallOpParam(True)
        call.makeCall(dest_uri, call_op)

        if debug:
            log_dir = _recordings_dir()
            log_path = log_dir / f"app_ai_realtime_call_debug_{int(time.time())}.log"
            try:
                call._debug_file = open(log_path, "w", encoding="utf-8")
                call._debug_lock = threading.Lock()
                call._debug_log("call_started", f"dest={dest_uri}")
                print(f"[AiRealtimeCall] Debug log: {log_path}")
            except Exception as e:
                print(f"[AiRealtimeCall] Could not open debug log: {e}")

        end_requested = threading.Event()

        def wait_enter() -> None:
            input()
            end_requested.set()

        prompted = False
        while not call.disconnected:
            session.endpoint.libHandleEvents(50)
            # Play any AI responses queued by the IO thread.
            call.process_pending_responses()
            if call.state_confirmed and not prompted:
                prompted = True
                print("Call connected to AI Realtime. Press Enter to end call.")
                t = threading.Thread(target=wait_enter, daemon=True)
                t.start()
            if end_requested.is_set():
                call.hangup(pj.CallOpParam(True))
                break

        while not call.disconnected:
            session.endpoint.libHandleEvents(50)
            call.process_pending_responses()

        return 0 if call.state_confirmed else 1
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"Call failed: {exc}")
        return 1
    finally:
        try:
            c = call
        except NameError:
            c = None
        if c is not None and getattr(c, "_debug_file", None) is not None:
            try:
                c._debug_file.close()
            except Exception:
                pass
            c._debug_file = None
        c = None
        try:
            call = None
        except NameError:
            pass
        # Release call before destroying session so pj.Call destructor runs while pjsua is still valid.
        session.destroy()
        # Clean up tmp directory (unless saving recordings for analysis)
        if not save_recordings:
            tmp = _tmp_dir()
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        elif save_recordings:
            print(f"[AiRealtimeCall] Recordings saved to: {_recordings_dir()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Make a phone call and attach an AI assistant using the OpenAI "
            "Realtime API (full-duplex audio via WebSocket)."
        )
    )
    parser.add_argument(
        "phone_number",
        type=str,
        help="Destination phone number (e.g. 0035123456789 or extension)",
    )
    parser.add_argument(
        "--reg-timeout",
        type=int,
        default=15,
        help="Registration timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--system-message",
        type=str,
        default=(
            "You are a helpful AI assistant on a phone call. The caller may speak in "
            "English or Spanish. First, detect the caller's language and always reply "
            "in that same language. Listen carefully to the caller, provide clear and "
            "concise answers, and ask a short clarifying question if needed. Keep "
            "responses brief and easy to understand over the phone."
        ),
        help="System/prompt message for the AI assistant.",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="alloy",
        help="OpenAI voice name to use (default: alloy).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-realtime",
        help="OpenAI Realtime model to use (default: gpt-realtime, override with OPENAI_RT_MODEL env).",
    )
    parser.add_argument(
        "--silence-duration",
        type=int,
        default=1000,
        help="How long (ms) server VAD waits after silence before AI responds (default: 1000).",
    )
    parser.add_argument(
        "--vad-threshold",
        type=float,
        default=0.5,
        help="Server VAD activation threshold, 0.0-1.0 (default: 0.5).",
    )
    parser.add_argument(
        "--prefix-padding",
        type=int,
        default=300,
        help="Audio to include before detected speech, in ms (default: 300).",
    )
    parser.add_argument(
        "--save-recordings",
        action="store_true",
        help="Save all recordings (full call, responses) to recordings/ directory for analysis.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write a timestamped event log to recordings/app_ai_realtime_call_debug_<ts>.log for debugging call flow.",
    )
    args = parser.parse_args()

    return run_call(
        phone_number=args.phone_number,
        reg_timeout=args.reg_timeout,
        system_message=args.system_message,
        voice=args.voice,
        model=args.model,
        silence_duration_ms=args.silence_duration,
        vad_threshold=args.vad_threshold,
        prefix_padding_ms=args.prefix_padding,
        save_recordings=args.save_recordings,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
