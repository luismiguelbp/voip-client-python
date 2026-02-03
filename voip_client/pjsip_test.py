"""
PJSIP basic test: import, create endpoint, start, destroy.

Usage:
    python -m voip_client.pjsip_test
"""

import sys

try:
    from voip_client.pjsip_common import PjsipEndpoint
except Exception as exc:
    print(f"Failed to import: {exc}")
    sys.exit(1)


def main() -> int:
    ep = PjsipEndpoint()
    try:
        ep.create()
        print("PJSIP init OK")
        return 0
    except Exception as exc:
        print(f"PJSIP init failed: {exc}")
        return 1
    finally:
        ep.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
