"""
Echo Test: call echo number (#124), hear yourself, record to recordings/, Enter or duration to hang up.

Usage:
    python -m voip_client.voip_echo_test [destination] [--duration SECS]

Records to recordings/voip_echo_test_YYYYMMDD_HHMMSS.wav.
Default destination: 124 or SIP_ECHO_EXTENSION in .env.
"""

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.voip_common import BaseVoipCall, VoipAccount, VoipSession


class VoipEchoTestCall(BaseVoipCall):
    """Echo Test (#124): connect audio, recording to recordings/."""

    def __init__(self, account: VoipAccount, record_path: Path) -> None:
        super().__init__(account)
        self.record_path = record_path

    def _cleanup_media(self) -> None:
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
            print("[trace] media active (echo)")
            try:
                self._recorder = pj.AudioMediaRecorder()
                self._recorder.createRecorder(str(self.record_path))
                self._cap_med.startTransmit(self._recorder)
                self._aud_med.startTransmit(self._recorder)
                print(f"[trace] recording started: {self.record_path}")
                print(f"Recording to: {self.record_path}")
            except Exception as e:
                print(f"Could not start recorder: {e}")
            self._media_setup_done = True
            break


def _recordings_dir() -> Path:
    """Return (and create) recordings/ under the project root."""
    d = Path(__file__).resolve().parent.parent / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_default_record_path() -> Path:
    """Timestamped WAV path in recordings/."""
    return _recordings_dir() / f"voip_echo_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"


def run_echo_test(
    destination: str,
    duration_seconds: Optional[int],
    record_path: Path,
    reg_timeout: int,
) -> int:
    session = VoipSession()
    try:
        print("[trace] creating endpoint and account")
        session.create_endpoint()
        session.create_account()
        print(f"[trace] waiting for registration (timeout={reg_timeout}s)")
        if not session.wait_registration(reg_timeout):
            print("Registration timed out")
            return 1
        print("[trace] registration OK")

        dest_uri = session.build_uri(destination)
        print(f"Calling {dest_uri} ...")
        print("[trace] placing call")
        call = VoipEchoTestCall(session.account, record_path)
        call_op = pj.CallOpParam(True)
        call.makeCall(dest_uri, call_op)

        end_requested = threading.Event()
        start_time: Optional[float] = None

        def wait_enter():
            input()
            end_requested.set()

        prompted = False
        while not call.disconnected:
            session.endpoint.libHandleEvents(50)
            if call.state_confirmed and not prompted:
                prompted = True
                start_time = time.time()
                print("[trace] call connected")
                if duration_seconds is not None:
                    print(
                        f"Call connected. Speak to hear echo. Press Enter to end or wait {duration_seconds}s."
                    )
                else:
                    print("Call connected. Speak to hear echo. Press Enter to end call.")
                t = threading.Thread(target=wait_enter, daemon=True)
                t.start()
            if end_requested.is_set():
                call.hangup(pj.CallOpParam(True))
                break
            if (
                duration_seconds is not None
                and start_time is not None
                and (time.time() - start_time) >= duration_seconds
            ):
                call.hangup(pj.CallOpParam(True))
                break

        while not call.disconnected:
            session.endpoint.libHandleEvents(50)

        print("[trace] call disconnected")
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
        description="Echo Test: call echo number (#124), recording to recordings/, Enter or --duration to hang up"
    )
    parser.add_argument(
        "destination",
        type=str,
        nargs="?",
        default=None,
        help="Destination extension (default: 124 or SIP_ECHO_EXTENSION)",
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=5,
        help="Auto-hangup after N seconds (default: 5)",
    )
    parser.add_argument(
        "--reg-timeout",
        type=int,
        default=15,
        help="Registration timeout in seconds (default: 15)",
    )
    args = parser.parse_args()

    destination = args.destination or os.getenv("SIP_ECHO_EXTENSION", "124")
    record_path = get_default_record_path()
    return run_echo_test(
        destination, args.duration, record_path, args.reg_timeout
    )


if __name__ == "__main__":
    raise SystemExit(main())
