"""
Common VoIP session, account, and call base classes.

Shared by app_phone_call, app_echo_call, app_ai_chatbot_call, app_ai_realtime_call,
voip_test_call, voip_echo_test, voip_dtmf_test, and pjsip_test_voip.
All use the same .env SIP credentials.
"""

import os
import time
from pathlib import Path
from typing import Any, Optional, Tuple

try:
    import pjsua2 as pj
except Exception:
    pj = None


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
    if pj is None:
        raise RuntimeError("pjsua2 not available")
    transport = transport.lower()
    if transport == "tcp":
        return pj.PJSIP_TRANSPORT_TCP, port or 5060
    if transport == "tls":
        return pj.PJSIP_TRANSPORT_TLS, port or 5061
    return pj.PJSIP_TRANSPORT_UDP, port or 5060


class VoipAccount(pj.Account):
    """SIP account with endpoint reference for media access in callbacks."""

    def __init__(self, endpoint: "pj.Endpoint") -> None:
        super().__init__()
        self.ep = endpoint
        self.reg_status: Optional[int] = None
        self.reg_reason: Optional[str] = None

    def onRegState(self, prm: Any) -> None:
        info = self.getInfo()
        self.reg_status = info.regStatus
        self.reg_reason = (
            getattr(info, "regReason", None)
            or getattr(info, "regStatusText", None)
            or ""
        )
        if getattr(info, "regIsActive", True):
            print(f"Registration: {self.reg_status} {self.reg_reason}")


class VoipSession:
    """Encapsulates endpoint, transport, and account; handles registration."""

    # Default public STUN server for NAT traversal
    DEFAULT_STUN_SERVER = "stun.l.google.com:19302"

    def __init__(self, env_path: Path = Path(".env")) -> None:
        load_env_file(env_path)
        self._sip_domain = require_env("SIP_DOMAIN")
        self._sip_username = require_env("SIP_USERNAME")
        self._sip_password = require_env("SIP_PASSWORD")
        self._sip_auth_username = (
            os.getenv("SIP_AUTH_USERNAME", "").strip() or self._sip_username
        )
        self._sip_transport = os.getenv("SIP_TRANSPORT", "udp").strip().lower()
        self._sip_proxy = os.getenv("SIP_PROXY", "").strip()
        sip_port = os.getenv("SIP_PORT", "").strip()
        self._sip_port = int(sip_port) if sip_port else None
        # STUN server for NAT traversal (can be disabled by setting to empty string)
        stun_env = os.getenv("STUN_SERVER", "").strip()
        if stun_env == "":
            # Not set in env: use default
            self._stun_server = self.DEFAULT_STUN_SERVER
        elif stun_env.lower() == "none" or stun_env.lower() == "disabled":
            # Explicitly disabled
            self._stun_server = None
        else:
            self._stun_server = stun_env
        self.endpoint: Optional[pj.Endpoint] = None
        self.account: Optional[VoipAccount] = None

    def create_endpoint(
        self,
        *,
        no_vad: bool = False,
        use_sw_clock: bool = False,
        thread_cnt: int = 0,
    ) -> None:
        transport_type, transport_port = transport_info(
            self._sip_transport, self._sip_port
        )
        self.endpoint = pj.Endpoint()
        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = thread_cnt
        # When no worker threads, process everything on the main thread.
        # With worker threads (e.g. headless AI calls), let PJSIP manage timing.
        ep_cfg.uaConfig.mainThreadOnly = (thread_cnt == 0)

        # Configure STUN server for NAT traversal
        if self._stun_server:
            ep_cfg.uaConfig.stunServer.append(self._stun_server)
            print(f"STUN server: {self._stun_server}")

        if no_vad:
            ep_cfg.medConfig.noVad = True
        if use_sw_clock:
            ep_cfg.medConfig.sndUseSwClock = True
        self.endpoint.libCreate()
        self.endpoint.libInit(ep_cfg)
        transport_cfg = pj.TransportConfig()
        transport_cfg.port = transport_port
        self.endpoint.transportCreate(transport_type, transport_cfg)
        self.endpoint.libStart()

    def create_account(self) -> VoipAccount:
        if self.endpoint is None:
            raise RuntimeError("create_endpoint() must be called first")
        acc_cfg = pj.AccountConfig()
        acc_cfg.idUri = normalize_sip_uri(f"{self._sip_username}@{self._sip_domain}")
        acc_cfg.regConfig.registrarUri = normalize_sip_uri(self._sip_domain)
        cred = pj.AuthCredInfo(
            "digest", "*", self._sip_auth_username, 0, self._sip_password
        )
        acc_cfg.sipConfig.authCreds.append(cred)
        if self._sip_proxy:
            acc_cfg.sipConfig.proxies.append(normalize_sip_uri(self._sip_proxy))

        # NAT traversal settings
        if self._stun_server:
            acc_cfg.mediaConfig.transportConfig.qosType = pj.PJ_QOS_TYPE_VOICE
            # Disable ICE to keep SDP compact (avoids "513 Message too big")
            # We still use STUN for public IP discovery and address rewriting.
            acc_cfg.natConfig.iceEnabled = False
            # Use STUN for NAT type detection and address discovery
            acc_cfg.natConfig.sipStunUse = pj.PJSUA_STUN_USE_DEFAULT
            acc_cfg.natConfig.mediaStunUse = pj.PJSUA_STUN_USE_DEFAULT
            # Rewrite headers/SDP to use public IP discovered via STUN
            acc_cfg.natConfig.contactRewriteUse = 1
            acc_cfg.natConfig.viaRewriteUse = 1
            acc_cfg.natConfig.sdpNatRewriteUse = 1
            print("NAT traversal: STUN enabled (ICE disabled for compact SDP)")

        self.account = VoipAccount(self.endpoint)
        self.account.create(acc_cfg)
        return self.account

    def wait_registration(self, timeout_seconds: int) -> bool:
        if self.endpoint is None or self.account is None:
            return False
        start = time.time()
        while time.time() - start < timeout_seconds:
            self.endpoint.libHandleEvents(50)
            if self.account.reg_status is None or self.account.reg_status == 0:
                continue
            if 200 <= self.account.reg_status < 300:
                return True
            return False
        return False

    def destroy(self) -> None:
        """Shut down the PJSIP library and release resources."""
        if self.endpoint is not None:
            try:
                self.endpoint.libDestroy()
            except Exception:
                pass
            self.endpoint = None
        self.account = None

    def build_uri(self, destination: str) -> str:
        return normalize_sip_uri(f"{destination}@{self._sip_domain}")


class BaseVoipCall(pj.Call):
    """Base call: tracks state, media setup flag, cleanup on disconnect."""

    def __init__(self, account: VoipAccount) -> None:
        super().__init__(account)
        self._account = account
        self.state_confirmed = False
        self.disconnected = False
        self._media_setup_done = False
        self._cap_med: Any = None
        self._aud_med: Any = None
        self._recorder: Any = None
        self._player: Any = None

    def onCallState(self, prm: Any) -> None:
        ci = self.getInfo()
        state = ci.state
        if state == pj.PJSIP_INV_STATE_CONFIRMED:
            self.state_confirmed = True
        if state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.disconnected = True
            self._cleanup_media()

    def _cleanup_media(self) -> None:
        self._cap_med = None
        self._aud_med = None
        self._media_setup_done = False

    def _connect_audio_to_call(
        self, aud_med: Any, cap_med: Any, play_med: Any
    ) -> None:
        cap_med.startTransmit(aud_med)
        aud_med.startTransmit(play_med)
