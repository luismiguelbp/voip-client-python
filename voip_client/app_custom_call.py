"""
Custom outbound call: play WAV when answered, record call, hang up on Enter.

Usage:
    python -m voip_client.app_custom_call <phone_number> [--audio WAV_PATH] [--output WAV_PATH]

By default plays a 5-second demo WAV (8 kHz mono). Use --audio to supply your own file.

Requires .env with SIP_DOMAIN, SIP_USERNAME, SIP_PASSWORD.
"""

import math
import struct
import sys
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.voip_common import BaseVoipCall, VoipAccount, VoipSession


class CustomOutboundCall(BaseVoipCall):
    """Outbound call with optional WAV play and recording."""

    def __init__(
        self,
        account: VoipAccount,
        audio_path: Optional[Path],
        record_path: Path,
    ) -> None:
        super().__init__(account)
        self.audio_path = audio_path
        self.record_path = record_path

    def _cleanup_media(self) -> None:
        if self._player is not None:
            try:
                player_id = self._player.getPortId()
                self._account.ep.mediaRemove(player_id)
            except Exception:
                pass
            try:
                del self._player
            except Exception:
                pass
            self._player = None
        if self._recorder is not None:
            try:
                if self._cap_med is not None:
                    self._cap_med.stopTransmit(self._recorder)
                if self._aud_med is not None:
                    self._aud_med.stopTransmit(self._recorder)
            except Exception:
                pass
            try:
                rec_id = self._recorder.getPortId()
                self._account.ep.mediaRemove(rec_id)
            except Exception:
                pass
            try:
                del self._recorder
            except Exception:
                pass
            self._recorder = None
        super()._cleanup_media()

    def onCallMediaState(self, prm) -> None:
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
            play_med = mgr.getPlaybackDevMedia()
            self._cap_med = cap_med
            self._aud_med = aud_med
            self._connect_audio_to_call(aud_med, cap_med, play_med)
            if self.audio_path and self.audio_path.exists():
                try:
                    self._player = pj.AudioMediaPlayer()
                    no_loop = getattr(pj, "PJMEDIA_FILE_NO_LOOP", 0)
                    self._player.createPlayer(str(self.audio_path), no_loop)
                    self._player.startTransmit(aud_med)
                    print(f"Playing audio: {self.audio_path}")
                except Exception as e:
                    print(f"Could not play WAV: {e}")
            try:
                self._recorder = pj.AudioMediaRecorder()
                self._recorder.createRecorder(str(self.record_path))
                self._cap_med.startTransmit(self._recorder)
                self._aud_med.startTransmit(self._recorder)
                print(f"Recording to: {self.record_path}")
            except Exception as e:
                print(f"Could not start recorder: {e}")
            self._media_setup_done = True
            break


def get_default_record_path() -> Path:
    project_root = Path(__file__).parent.parent
    recordings_dir = project_root / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return recordings_dir / f"call_{timestamp}.wav"


DEMO_WAV_DURATION_SEC = 5
DEMO_WAV_SAMPLE_RATE = 8000
DEMO_WAV_FILENAME = "demo_5s.wav"


def _generate_demo_wav(path: Path, duration_sec: float = DEMO_WAV_DURATION_SEC) -> None:
    """Write a 5-second 8 kHz mono 16-bit PCM WAV (soft tone) to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(DEMO_WAV_SAMPLE_RATE * duration_sec)
    freq_hz = 440
    amplitude = 8000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(DEMO_WAV_SAMPLE_RATE)
        for i in range(n_frames):
            t = i / DEMO_WAV_SAMPLE_RATE
            sample = int(amplitude * math.sin(2 * math.pi * freq_hz * t))
            wav.writeframes(struct.pack("<h", max(-32768, min(32767, sample))))


def get_default_demo_audio_path() -> Path:
    """Return path to default 5-second demo WAV, creating it if missing."""
    static_dir = Path(__file__).parent / "static"
    path = static_dir / DEMO_WAV_FILENAME
    if not path.exists():
        _generate_demo_wav(path)
    return path


def run_call(
    phone_number: str,
    audio_path: Optional[Path],
    record_path: Path,
    reg_timeout: int,
) -> int:
    session = VoipSession()
    try:
        session.create_endpoint()
        session.create_account()
        if not session.wait_registration(reg_timeout):
            print("Registration timed out")
            return 1

        dest_uri = session.build_uri(phone_number)
        print(f"Calling {dest_uri} ...")
        call = CustomOutboundCall(session.account, audio_path, record_path)
        call_op = pj.CallOpParam(True)
        call.makeCall(dest_uri, call_op)

        end_requested = threading.Event()

        def wait_enter():
            input()
            end_requested.set()

        prompted = False
        while not call.disconnected:
            session.endpoint.libHandleEvents(50)
            if call.state_confirmed and not prompted:
                prompted = True
                print("Call connected. Press Enter to end call and save recording.")
                t = threading.Thread(target=wait_enter, daemon=True)
                t.start()
            if end_requested.is_set():
                call.hangup(pj.CallOpParam(True))
                break

        while not call.disconnected:
            session.endpoint.libHandleEvents(50)

        if record_path.exists():
            size_kb = record_path.stat().st_size / 1024
            print(f"Recording saved: {record_path} ({size_kb:.1f} KB)")
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"Call failed: {exc}")
        return 1
    finally:
        try:
            call = None
        except NameError:
            pass
        session.destroy()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Call a number, play optional WAV, record call, hang up on Enter"
    )
    parser.add_argument(
        "phone_number",
        type=str,
        help="Destination phone number (e.g. 0035123456789 or extension)",
    )
    parser.add_argument(
        "--audio",
        "-a",
        type=str,
        default=None,
        help="Path to WAV file to play when call is answered (default: 5-second demo WAV)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Path to save call recording (default: recordings/call_YYYYMMDD_HHMMSS.wav)",
    )
    parser.add_argument(
        "--reg-timeout",
        type=int,
        default=15,
        help="Registration timeout in seconds (default: 15)",
    )
    args = parser.parse_args()

    if args.audio:
        audio_path = Path(args.audio)
        if not audio_path.exists():
            print(f"Error: Audio file not found: {audio_path}")
            return 1
    else:
        audio_path = get_default_demo_audio_path()

    record_path = Path(args.output) if args.output else get_default_record_path()
    if record_path.parent and not record_path.parent.exists():
        record_path.parent.mkdir(parents=True, exist_ok=True)

    return run_call(args.phone_number, audio_path, record_path, args.reg_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
