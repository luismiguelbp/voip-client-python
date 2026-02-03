"""
Common PJSIP types for pjsip_* test scripts.

Provides a minimal endpoint (no SIP account) used by pjsip_test and pjsip_test_audio.
"""

from typing import Optional

try:
    import pjsua2 as pj
except Exception:
    pj = None


class PjsipEndpoint:
    """Minimal PJSIP endpoint for tests: init, UDP transport, no account."""

    def __init__(self) -> None:
        self.endpoint: Optional["pj.Endpoint"] = None

    def create(self) -> None:
        if pj is None:
            raise RuntimeError("pjsua2 not available")
        ep = pj.Endpoint()
        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = 0
        ep_cfg.uaConfig.mainThreadOnly = True
        ep_cfg.logConfig.level = 3
        ep_cfg.logConfig.consoleLevel = 3
        ep.libCreate()
        ep.libInit(ep_cfg)
        transport_cfg = pj.TransportConfig()
        transport_cfg.port = 0
        ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_cfg)
        ep.libStart()
        self.endpoint = ep

    def destroy(self) -> None:
        if self.endpoint is not None:
            try:
                self.endpoint.libDestroy()
            except Exception:
                pass
            self.endpoint = None
