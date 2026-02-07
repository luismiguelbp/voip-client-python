"""
DTMF Test: call DTMF test number (#125), send DTMF digits, then hang up.

Usage:
    python -m voip_client.voip_dtmf_test [destination] [--digits "1234567890#*"] [--digit-delay-ms MS]

Default destination: 125 or SIP_DTMF_TEST_EXTENSION in .env.
"""

import os
import sys
import time
from typing import Optional

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.voip_common import BaseVoipCall, VoipAccount, VoipSession


class VoipDtmfTestCall(BaseVoipCall):
    """DTMF Test: connect audio, send DTMF sequence."""

    def __init__(
        self,
        account: VoipAccount,
        digits: str,
        digit_delay_ms: int,
    ) -> None:
        super().__init__(account)
        self.digits = digits
        self.digit_delay_ms = digit_delay_ms
        self.media_ready = False
        self.dtmf_done = False
        self._digit_index = 0
        self._next_digit_time: Optional[float] = None

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
            self._media_setup_done = True
            self.media_ready = True
            print("[trace] media active, DTMF ready")
            break


def run_dtmf_test(
    destination: str,
    digits: str,
    digit_delay_ms: int,
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
        call = VoipDtmfTestCall(session.account, digits, digit_delay_ms)
        call_op = pj.CallOpParam(True)
        call.makeCall(dest_uri, call_op)

        post_dtmf_delay_s = 2.0
        rfc2833 = getattr(pj, "PJSUA_DTMF_METHOD_RFC2833", 1)
        hangup_after: Optional[float] = None

        while not call.disconnected:
            session.endpoint.libHandleEvents(50)

            if hangup_after is not None:
                if time.time() >= hangup_after:
                    call.hangup(pj.CallOpParam(True))
                    hangup_after = None
                continue

            if not call.media_ready or call.dtmf_done:
                continue

            now = time.time()
            if call._next_digit_time is not None and now < call._next_digit_time:
                continue
            if call._digit_index >= len(call.digits):
                call.dtmf_done = True
                hangup_after = now + post_dtmf_delay_s
                continue
            digit = call.digits[call._digit_index]
            try:
                prm = pj.CallSendDtmfParam()
                prm.method = rfc2833
                prm.digits = digit
                call.sendDtmf(prm)
                call._digit_index += 1
                call._next_digit_time = time.time() + (call.digit_delay_ms / 1000.0)
            except Exception as e:
                if "RFC2833" in str(e) or "2833" in str(e):
                    print(
                        "Remote does not support RFC 2833 DTMF; cannot send digits."
                    )
                else:
                    print(f"DTMF send error: {e}")
                call.dtmf_done = True
                hangup_after = time.time() + post_dtmf_delay_s

        while not call.disconnected:
            session.endpoint.libHandleEvents(50)

        print("[trace] call disconnected")
        print("DTMF test complete.")
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
        description="DTMF Test: call DTMF test number (#125), send digits, then hang up"
    )
    parser.add_argument(
        "destination",
        type=str,
        nargs="?",
        default=None,
        help="Destination extension (default: 125 or SIP_DTMF_TEST_EXTENSION)",
    )
    parser.add_argument(
        "--digits",
        type=str,
        default="1234567890#*",
        help='DTMF digits to send (default: "1234567890#*")',
    )
    parser.add_argument(
        "--digit-delay-ms",
        type=int,
        default=200,
        help="Delay between digits in ms (default: 200)",
    )
    parser.add_argument(
        "--reg-timeout",
        type=int,
        default=15,
        help="Registration timeout in seconds (default: 15)",
    )
    args = parser.parse_args()

    destination = args.destination or os.getenv("SIP_DTMF_TEST_EXTENSION", "125")
    return run_dtmf_test(
        destination, args.digits, args.digit_delay_ms, args.reg_timeout
    )


if __name__ == "__main__":
    raise SystemExit(main())
