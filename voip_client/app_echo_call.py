"""
Echo test app: call a phone number and route microphone to speaker so you hear yourself.

Usage:
    python -m voip_client.app_echo_call <phone_number> [--duration SECS]

Records to recordings/app_echo_call_YYYYMMDD_HHMMSS.wav.
Requires .env with SIP_DOMAIN, SIP_USERNAME, SIP_PASSWORD.
"""

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.voip_common import BaseVoipCall, VoipAccount, VoipSession


class AppEchoCall(BaseVoipCall):
    """Call with mic-to-speaker echo (hear yourself) and recording to recordings/."""

    def __init__(
        self,
        account: VoipAccount,
        record_path: Path,
        debug_file=None,
        debug_lock: Optional[threading.Lock] = None,
    ) -> None:
        super().__init__(account)
        self.record_path = record_path
        self._debug_file = debug_file
        self._debug_lock = debug_lock

    def _debug_log(self, event: str, detail: str = "") -> None:
        """Write a timestamped event line to the debug log file if --debug was used."""
        if not self._debug_file or not self._debug_lock:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        line = f"{ts} {event} {detail}\n"
        try:
            with self._debug_lock:
                self._debug_file.write(line)
                self._debug_file.flush()
        except Exception:
            pass

    def _cleanup_media(self) -> None:
        self._debug_log("call_disconnected", "")
        if self._recorder is not None:
            try:
                if self._cap_med is not None:
                    self._cap_med.stopTransmit(self._recorder)
                if self._aud_med is not None:
                    self._aud_med.stopTransmit(self._recorder)
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
            cap_med.startTransmit(play_med)
            try:
                self._recorder = pj.AudioMediaRecorder()
                self._recorder.createRecorder(str(self.record_path))
                self._cap_med.startTransmit(self._recorder)
                self._aud_med.startTransmit(self._recorder)
                self._debug_log("media_ready", f"record_path={self.record_path}")
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
    return _recordings_dir() / f"app_echo_call_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"


def run_echo_test(
    phone_number: str,
    duration_seconds: Optional[int],
    record_path: Path,
    reg_timeout: int,
    debug: bool = False,
) -> int:
    session = VoipSession()
    debug_file = None
    debug_lock = threading.Lock() if debug else None
    try:
        session.create_endpoint()
        session.create_account()
        if not session.wait_registration(reg_timeout):
            print("Registration timed out")
            return 1

        dest_uri = session.build_uri(phone_number)
        print(f"Calling {dest_uri} ...")
        if debug:
            log_path = _recordings_dir() / f"app_echo_call_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            try:
                debug_file = open(log_path, "w", encoding="utf-8")
                print(f"Debug log: {log_path}")
            except Exception as e:
                print(f"Could not open debug log: {e}")
                debug_file = None

        call = AppEchoCall(session.account, record_path, debug_file=debug_file, debug_lock=debug_lock)
        call_op = pj.CallOpParam(True)
        call.makeCall(dest_uri, call_op)
        if debug and debug_file:
            call._debug_log("call_started", f"dest={dest_uri}")

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
                if duration_seconds is not None:
                    print(
                        f"Call connected. You will hear yourself (echo). Press Enter to end or wait {duration_seconds}s."
                    )
                else:
                    print("Call connected. You will hear yourself (echo). Press Enter to end call.")
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

        if record_path.exists():
            size_kb = record_path.stat().st_size / 1024
            if debug and debug_file:
                call._debug_log("recording_saved", f"path={record_path} size_kb={size_kb:.1f}")
            print(f"Recording saved: {record_path} ({size_kb:.1f} KB)")
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"Call failed: {exc}")
        return 1
    finally:
        if debug_file is not None:
            try:
                debug_file.close()
            except Exception:
                pass
        try:
            call = None
        except NameError:
            pass
        session.destroy()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Call a number and route mic to speaker (echo test); Enter or --duration to hang up"
    )
    parser.add_argument(
        "phone_number",
        type=str,
        help="Destination phone number or extension",
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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write a timestamped event log to recordings/app_echo_call_debug_<ts>.log for debugging.",
    )
    args = parser.parse_args()

    record_path = get_default_record_path()
    return run_echo_test(
        args.phone_number, args.duration, record_path, args.reg_timeout, debug=args.debug
    )


if __name__ == "__main__":
    raise SystemExit(main())
