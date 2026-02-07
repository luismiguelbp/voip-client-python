"""
VoIPstudio registration test: register with SIP credentials from .env.

Usage:
    python -m voip_client.pjsip_test_voip

Requires .env with SIP_DOMAIN, SIP_USERNAME, SIP_PASSWORD.
"""

import os
import sys
from pathlib import Path

try:
    from voip_client.voip_common import VoipSession
except Exception as exc:
    print(f"Failed to import: {exc}")
    sys.exit(1)


def main() -> int:
    reg_timeout = int(os.getenv("SIP_REG_TIMEOUT", "15"))
    session = VoipSession(Path(".env"))
    try:
        print("[trace] creating endpoint")
        session.create_endpoint()
        print("[trace] creating account")
        session.create_account()
        print(f"[trace] waiting for registration (timeout={reg_timeout}s)")
        if session.wait_registration(reg_timeout):
            print("[trace] registration OK")
            return 0
        print("Registration timed out")
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"Registration failed: {exc}")
        return 1
    finally:
        print("[trace] destroying session")
        session.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
