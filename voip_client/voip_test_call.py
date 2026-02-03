"""
Test Call: call provider test number (#123), connect audio, optional record, hang up on Enter.

Usage:
    python -m voip_client.voip_test_call [destination] [--output WAV_PATH]

Default destination: 123 or SIP_TEST_CALL_EXTENSION in .env.
"""

import os
import sys
import threading
from pathlib import Path
from typing import Optional

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.voip_common import BaseVoipCall, VoipAccount, VoipSession


class VoipTestCall(BaseVoipCall):
    """Test Call (#123): connect audio, optional recording."""

    def __init__(
        self,
        account: VoipAccount,
        record_path: Optional[Path],
    ) -> None:
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
            if self.record_path is not None:
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


def run_test_call(
    destination: str,
    record_path: Optional[Path],
    reg_timeout: int,
) -> int:
    session = VoipSession()
    try:
        session.create_endpoint()
        session.create_account()
        if not session.wait_registration(reg_timeout):
            print("Registration timed out")
            return 1

        dest_uri = session.build_uri(destination)
        print(f"Calling {dest_uri} ...")
        call = VoipTestCall(session.account, record_path)
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
                print("Call connected. Press Enter to end call.")
                t = threading.Thread(target=wait_enter, daemon=True)
                t.start()
            if end_requested.is_set():
                call.hangup(pj.CallOpParam(True))
                break

        while not call.disconnected:
            session.endpoint.libHandleEvents(50)

        if record_path and record_path.exists():
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
        description="Test Call: call test number (#123), optional recording, Enter to hang up"
    )
    parser.add_argument(
        "destination",
        type=str,
        nargs="?",
        default=None,
        help="Destination extension (default: 123 or SIP_TEST_CALL_EXTENSION)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Path to save call recording",
    )
    parser.add_argument(
        "--reg-timeout",
        type=int,
        default=15,
        help="Registration timeout in seconds (default: 15)",
    )
    args = parser.parse_args()

    destination = args.destination or os.getenv("SIP_TEST_CALL_EXTENSION", "123")
    record_path = Path(args.output) if args.output else None
    if record_path and record_path.parent and not record_path.parent.exists():
        record_path.parent.mkdir(parents=True, exist_ok=True)

    return run_test_call(destination, record_path, args.reg_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
