"""
AI Assistant using Whisper STT + Chat Completions + OpenAI TTS.

Usage:
    python -m voip_client.app_ai_chatbot_call <phone_number> [--silence-duration MS]

This script:
- Registers to the SIP server using the same .env as the other app_* tools
- Places an outbound call to the given destination
- When media becomes active, attaches an AI Assistant bridge so the remote
  caller can have a natural, turn-based conversation with an AI assistant.

Pipeline:
- Speech-to-Text: Whisper API transcribes caller speech
- Reasoning: Chat Completions API generates responses
- Text-to-Speech: OpenAI TTS converts responses to audio

Architecture:
- All conference bridge ports are C-level (AudioMediaRecorder, AudioMediaPlayer).
- A Python thread bridges audio between WAV files and the AI assistant,
  completely outside the PJSIP media path.  This avoids the GIL deadlock
  and broken timing caused by Python AudioMediaPort on macOS ARM64.
"""

import argparse
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

from voip_client.whisper_assistant import WhisperAssistantBridge
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


def _recordings_dir() -> Path:
    """Return (and create) a recordings/ directory under the project root."""
    d = Path(__file__).resolve().parent.parent / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    """Write raw PCM16 mono bytes to a WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def _read_wav_sample_rate(path: Path) -> int:
    """Read sample rate from WAV file header (bytes 24-27)."""
    try:
        with open(path, "rb") as f:
            f.seek(24)  # Sample rate is at offset 24-27 in WAV header
            sample_rate = struct.unpack("<I", f.read(4))[0]
            return sample_rate if sample_rate > 0 else 8000
    except Exception:
        return 8000  # fallback to default


class AiBotCall(BaseVoipCall):
    """
    Outbound call whose remote party talks to a conversational AI Assistant.

    Uses the Whisper STT + Chat Completions + TTS pipeline.
    All conference bridge ports are C-level (recorder + player).
    A background Python thread bridges audio between WAV files and the AI.
    """

    def __init__(
        self,
        account: VoipAccount,
        system_message: str,
        voice: str,
        model: str = "gpt-4o",
        silence_duration_ms: int = 1000,
        save_recordings: bool = False,
    ) -> None:
        super().__init__(account)
        self._bridge = WhisperAssistantBridge(
            system_message=system_message,
            voice=voice,
            model=model,
            silence_duration_ms=silence_duration_ms,
            save_recordings=save_recordings,
        )
        self._sample_rate = 8000
        self._io_stop = threading.Event()
        self._io_thread: Optional[threading.Thread] = None
        self._recording_path: Optional[Path] = None
        self._save_recordings = save_recordings
        self._recordings_dir: Optional[Path] = None
        if save_recordings:
            call_ts = int(time.time())
            self._recordings_dir = _recordings_dir() / f"app_ai_chatbot_call_{call_ts}"
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
        """Stop bridge and IO thread on disconnect."""
        self._debug_log("call_disconnected", "")
        self._io_stop.set()

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
                    print(f"[AiBotCall] Saved full call recording: {dest_path}")
                except Exception as exc:
                    print(f"[AiBotCall] Failed to save recording: {exc}")
                
                # Save full conversation transcript
                try:
                    transcript_path = self._recordings_dir / "full_transcript.txt"
                    messages = getattr(self._bridge, "_messages", [])
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write("=== Full Conversation Transcript ===\n\n")
                        for msg in messages:
                            role = msg.get("role", "unknown")
                            content = msg.get("content", "")
                            if role == "system":
                                f.write(f"[SYSTEM]: {content}\n\n")
                            elif role == "user":
                                f.write(f"[USER]: {content}\n\n")
                            elif role == "assistant":
                                f.write(f"[ASSISTANT]: {content}\n\n")
                    print(f"[AiBotCall] Saved full transcript: {transcript_path}")
                except Exception as exc:
                    print(f"[AiBotCall] Failed to save transcript: {exc}")

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
                self._recording_path = _tmp_dir() / f"app_ai_chatbot_call_rec_{ts}.wav"
                self._recorder = pj.AudioMediaRecorder()
                self._recorder.createRecorder(str(self._recording_path))
                aud_med.startTransmit(self._recorder)

                # Mute capture device; connection still needed to activate
                # the sound device (port 0) which drives the conference clock.
                cap_med.adjustTxLevel(0.0)
                cap_med.startTransmit(aud_med)

                # Start AI bridge and IO thread.
                self._bridge.set_sample_rate(self._sample_rate)
                if self._recordings_dir:
                    self._bridge.set_recordings_dir(self._recordings_dir)
                self._bridge.start()

                self._io_stop.clear()
                self._io_thread = threading.Thread(
                    target=self._audio_io_loop, daemon=True
                )
                self._io_thread.start()

                # Get the model name from the bridge for logging
                model_name = getattr(self._bridge, "_model", "unknown")
                print(
                    f"[AiBotCall] Media ready: sample_rate={self._sample_rate}, "
                    f"recording={self._recording_path}, model={model_name}"
                )
                self._debug_log("media_ready", f"sample_rate={self._sample_rate} model={model_name}")
            except Exception as exc:
                print(f"[AiBotCall] Failed to set up media: {exc}")
                return

            self._media_setup_done = True
            break

    # -----------------------------------------------------------------
    # Background IO thread -- runs entirely outside the PJSIP media path
    # -----------------------------------------------------------------

    def _audio_io_loop(self) -> None:
        """Read from the growing recorder WAV and feed the AI bridge.

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

        # Read the actual sample rate from the WAV file header
        actual_sample_rate = _read_wav_sample_rate(rec_path)
        file_sample_rate = actual_sample_rate
        needs_resampling = (file_sample_rate != self._sample_rate)
        
        if needs_resampling:
            print(
                f"[AiBotCall] Sample rate mismatch detected: "
                f"call_stream={self._sample_rate}Hz, file={file_sample_rate}Hz, "
                f"will resample file audio to match call stream rate"
            )
            # Import resampling function from bridge
            resample_func = self._bridge._resample_pcm
        else:
            resample_func = None

        # Bytes per frame: 20 ms of PCM16 mono at file sample rate
        # We read at file rate, then resample to call stream rate if needed
        frame_bytes_file = file_sample_rate * 2 // 50  # 320 @ 8 kHz, 640 @ 16 kHz
        frame_bytes_call = self._sample_rate * 2 // 50  # Target frame size for bridge

        try:
            f = open(rec_path, "rb")
        except OSError:
            return

        try:
            f.seek(_WAV_HEADER_SIZE)  # skip WAV header
            leftover = b""
            response_counter = 0

            while not self._io_stop.is_set():
                # --- Capture: read new audio from the recording file ---
                raw = f.read(frame_bytes_file * 10)  # read up to 200 ms at file rate
                if raw:
                    raw = leftover + raw
                    leftover = b""
                    # Split into complete frames at file rate
                    n_full = (len(raw) // frame_bytes_file) * frame_bytes_file
                    if n_full > 0:
                        for i in range(0, n_full, frame_bytes_file):
                            frame_at_file_rate = raw[i : i + frame_bytes_file]
                            # Resample if needed before sending to bridge
                            if needs_resampling and resample_func:
                                frame_at_call_rate = resample_func(
                                    frame_at_file_rate, file_sample_rate, self._sample_rate
                                )
                            else:
                                frame_at_call_rate = frame_at_file_rate
                            self._bridge.send_pcm(frame_at_call_rate)
                    leftover = raw[n_full:]

                # --- Response: drain bridge output ---
                response_pcm = bytearray()
                got_any = False
                while not self._io_stop.is_set():
                    chunk = self._bridge.recv_pcm(timeout=0.02)
                    if chunk:
                        response_pcm.extend(chunk)
                        got_any = True
                    else:
                        # No more chunks right now.
                        if got_any:
                            # Drain: wait a bit longer for trailing chunks.
                            trailing = self._bridge.recv_pcm(
                                timeout=_RESPONSE_DRAIN_TIMEOUT_S
                            )
                            if trailing:
                                response_pcm.extend(trailing)
                                continue  # keep draining
                        break  # nothing available

                if response_pcm and not self._io_stop.is_set():
                    # Hand off the response PCM to the main thread for playback.
                    try:
                        pcm_bytes = bytes(response_pcm)
                        self._pending_responses.put(
                            (pcm_bytes, response_counter), timeout=0.1
                        )
                        self._debug_log("response_queued", f"index={response_counter} bytes={len(pcm_bytes)}")
                        response_counter += 1
                    except Exception:
                        # Best-effort; drop response on failure.
                        pass

                # Brief sleep when there's nothing to read yet.
                if not raw and not response_pcm:
                    time.sleep(0.02)
        finally:
            f.close()

    def _play_response(self, pcm: bytes, index: int) -> None:
        """Write a response WAV and play it into the call."""
        if self._aud_med is None or self._io_stop.is_set():
            return

        wav_path = _tmp_dir() / f"app_ai_chatbot_call_resp_{int(time.time())}_{index}.wav"
        _write_wav(wav_path, pcm, self._sample_rate)

        duration_s = len(pcm) / (self._sample_rate * 2)
        print(f"[AiBotCall] Playing response {index} ({duration_s:.1f}s)")
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
            print(f"[AiBotCall] Playback error: {exc}")

        self._debug_log("response_play_end", f"index={index}")
        # Save response file if requested, otherwise clean up
        if self._save_recordings and self._recordings_dir:
            try:
                dest_path = self._recordings_dir / f"response_{index:03d}.wav"
                shutil.copy2(wav_path, dest_path)
            except Exception as exc:
                print(f"[AiBotCall] Failed to save response {index}: {exc}")
        
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
    save_recordings: bool = False,
    debug: bool = False,
) -> int:
    """Place a call and attach AI Assistant (Whisper pipeline) when media is active."""
    session = VoipSession()
    try:
        # threadCnt=0: main thread drives SIP events via libHandleEvents.
        # Real audio device (no setNullDev) provides correct conference bridge
        # timing at 50 fps.  All conference bridge ports are C-level.
        session.create_endpoint(no_vad=True)
        session.create_account()
        if not session.wait_registration(reg_timeout):
            print("Registration timed out")
            return 1

        dest_uri = session.build_uri(phone_number)
        print(f"Calling {dest_uri} (AI Bot - Whisper pipeline) ...")
        call = AiBotCall(
            session.account,
            system_message=system_message,
            voice=voice,
            model=model,
            silence_duration_ms=silence_duration_ms,
            save_recordings=save_recordings,
        )
        call_op = pj.CallOpParam(True)
        call.makeCall(dest_uri, call_op)

        if debug:
            log_dir = _recordings_dir()
            log_path = log_dir / f"app_ai_chatbot_call_debug_{int(time.time())}.log"
            try:
                call._debug_file = open(log_path, "w", encoding="utf-8")
                call._debug_lock = threading.Lock()
                call._debug_log("call_started", f"dest={dest_uri}")
                print(f"[AiBotCall] Debug log: {log_path}")
            except Exception as e:
                print(f"[AiBotCall] Could not open debug log: {e}")

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
                print("Call connected to AI Bot. Press Enter to end call.")
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
        session.destroy()
        # Clean up tmp directory (unless saving recordings for analysis)
        if not save_recordings:
            tmp = _tmp_dir()
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        elif save_recordings:
            print(f"[AiBotCall] Recordings saved to: {_recordings_dir()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Make a phone call and attach an AI Bot (Whisper STT + "
            "Chat Completions + OpenAI TTS) to the remote caller."
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
        help="System/prompt message for the AI Assistant.",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="alloy",
        help="OpenAI TTS voice name to use (default: alloy).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="OpenAI chat model to use (default: gpt-4o).",
    )
    parser.add_argument(
        "--silence-duration",
        type=int,
        default=1000,
        help="How long (ms) the caller must be silent before the assistant responds (default: 1000).",
    )
    parser.add_argument(
        "--save-recordings",
        action="store_true",
        help="Save all recordings (full call, speech segments, responses) to recordings/ directory for analysis.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write a timestamped event log to recordings/app_ai_chatbot_call_debug_<ts>.log for debugging call flow.",
    )
    args = parser.parse_args()

    return run_call(
        phone_number=args.phone_number,
        reg_timeout=args.reg_timeout,
        system_message=args.system_message,
        voice=args.voice,
        model=args.model,
        silence_duration_ms=args.silence_duration,
        save_recordings=args.save_recordings,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
