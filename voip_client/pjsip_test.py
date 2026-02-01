import sys

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)


def main() -> int:
    ep = pj.Endpoint()
    ep_cfg = pj.EpConfig()
    ep_cfg.uaConfig.threadCnt = 0  # Required for Python bindings

    try:
        ep.libCreate()
        ep.libInit(ep_cfg)

        transport_cfg = pj.TransportConfig()
        transport_cfg.port = 0  # Let OS select a free port
        ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_cfg)

        ep.libStart()
        print("PJSIP init OK")
    except Exception as exc:
        print(f"PJSIP init failed: {exc}")
        return 1
    finally:
        try:
            ep.libDestroy()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
