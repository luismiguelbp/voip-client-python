import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def normalize_sip_uri(value: str) -> str:
    if value.startswith("sip:"):
        return value
    return f"sip:{value}"


def transport_info(transport: str, port: Optional[int]) -> Tuple[int, int]:
    transport = transport.lower()
    if transport == "tcp":
        return pj.PJSIP_TRANSPORT_TCP, port or 5060
    if transport == "tls":
        return pj.PJSIP_TRANSPORT_TLS, port or 5061
    return pj.PJSIP_TRANSPORT_UDP, port or 5060


class VoipAccount(pj.Account):
    def __init__(self) -> None:
        super().__init__()
        self.reg_status: Optional[int] = None
        self.reg_reason: Optional[str] = None

    def onRegState(self, prm) -> None:
        info = self.getInfo()
        self.reg_status = info.regStatus
        self.reg_reason = info.regReason
        print(f"Registration: {self.reg_status} {self.reg_reason}")


def main() -> int:
    load_env_file(Path(".env"))

    try:
        sip_domain = require_env("SIP_DOMAIN")
        sip_username = require_env("SIP_USERNAME")
        sip_password = require_env("SIP_PASSWORD")
    except ValueError as exc:
        print(str(exc))
        return 1

    sip_auth_username = os.getenv("SIP_AUTH_USERNAME", "").strip() or sip_username
    sip_transport = os.getenv("SIP_TRANSPORT", "udp").strip().lower()
    sip_proxy = os.getenv("SIP_PROXY", "").strip()
    reg_timeout = int(os.getenv("SIP_REG_TIMEOUT", "15"))

    sip_port = os.getenv("SIP_PORT", "").strip()
    port_value = int(sip_port) if sip_port else None
    transport_type, transport_port = transport_info(sip_transport, port_value)

    ep = pj.Endpoint()
    ep_cfg = pj.EpConfig()
    ep_cfg.uaConfig.threadCnt = 0  # Required for Python bindings
    ep_cfg.uaConfig.mainThreadOnly = True

    acc = VoipAccount()
    try:
        ep.libCreate()
        ep.libInit(ep_cfg)

        transport_cfg = pj.TransportConfig()
        transport_cfg.port = transport_port
        ep.transportCreate(transport_type, transport_cfg)

        ep.libStart()

        acc_cfg = pj.AccountConfig()
        acc_cfg.idUri = normalize_sip_uri(f"{sip_username}@{sip_domain}")
        acc_cfg.regConfig.registrarUri = normalize_sip_uri(sip_domain)

        cred = pj.AuthCredInfo("digest", "*", sip_auth_username, 0, sip_password)
        acc_cfg.sipConfig.authCreds.append(cred)

        if sip_proxy:
            acc_cfg.sipConfig.proxies.append(normalize_sip_uri(sip_proxy))

        acc.create(acc_cfg)

        start = time.time()
        while time.time() - start < reg_timeout:
            ep.libHandleEvents(50)
            if acc.reg_status is None or acc.reg_status == 0:
                continue
            if 200 <= acc.reg_status < 300:
                return 0
            return 1

        print("Registration timed out")
        return 1
    except Exception as exc:
        print(f"Registration failed: {exc}")
        return 1
    finally:
        try:
            ep.libDestroy()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
