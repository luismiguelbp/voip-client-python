## VoIPstudio integration (PJSIP + Python)

This guide explains how to connect a Python client that uses PJSIP to a
VoIPstudio SIP account. It focuses on the data you must collect from your
VoIPstudio portal and how to map it to typical PJSIP settings.

In this project, SIP registration and calls use `voip_common` (VoipSession, BaseVoipCall). See the README for scripts: `pjsip_test_voip` (registration), `voip_test_call`, `voip_echo_test`, `voip_dtmf_test`, and `app_custom_call` / `app_echo_call`.

### Prerequisites

- A VoIPstudio account with at least one SIP user or device created
- SIP credentials for that user (username and password)
- A machine that can build and run PJSIP (see the `PJSIP_*.md` guides)
- A Python binding for PJSIP (for example, `pjsua2` or another wrapper)

### What you need from VoIPstudio

Collect these values from the VoIPstudio portal (names vary by portal page):

- SIP username (sometimes called user, extension, or device ID)
- SIP authentication username (if different from the SIP username)
- SIP password
- SIP domain or registrar (host name)
- SIP transport and port (UDP/TCP/TLS)
- Outbound proxy or SBC host (if required by your account)
- Optional: voicemail number, codec policy, or NAT traversal guidance

If you do not see a field, check the device or extension configuration page
for your user, or the account-wide SIP settings.

### Mapping VoIPstudio values to PJSIP

Use the values from the portal and map them to the fields below in your PJSIP
account configuration:

- `id_uri`: `sip:<sip_username>@<sip_domain>`
- `reg_uri`: `sip:<sip_domain>`
- `registrar`: same as `reg_uri`
- `auth_cred`: realm `*` or the registrar domain, user is the SIP auth username,
  password is the SIP password
- `proxy`: outbound proxy or SBC host if VoIPstudio requires it
- `transport`: set UDP/TCP/TLS and port to match the portal values

### Basic connection flow

1. Initialize the PJSIP endpoint and set logging.
2. Create a transport (UDP/TCP/TLS).
3. Create an account with the SIP credentials from VoIPstudio.
4. Register the account and confirm registration success.
5. Create calls using SIP URIs such as `sip:<destination>@<sip_domain>`.

### Example (pseudocode)

This is intentionally high level because projects vary in the PJSIP Python
binding used. Adjust to your wrapper's API.

```
endpoint = pjsip.Endpoint()
endpoint.libCreate()
endpoint.libInit(log_level=4)

transport = endpoint.transportCreate("udp", port=0)

account_cfg = {
  "id_uri": "sip:1001@your_sip_domain",
  "reg_uri": "sip:your_sip_domain",
  "auth": {
    "username": "1001",
    "password": "your_password",
    "realm": "*",
  },
  "proxy": ["sip:your_outbound_proxy"],
}

account = endpoint.accountCreate(account_cfg)
account.register()

call = account.makeCall("sip:destination@your_sip_domain")
```

### Troubleshooting

- Registration fails: verify the SIP domain, username, and password, and
  ensure your IP is allowed if the account has IP restrictions.
- No audio or one-way audio: confirm NAT traversal settings and use a STUN
  server or outbound proxy if required.
- TLS errors: verify certificates and the TLS port configured by VoIPstudio.

### Related docs

- `docs/PJSIP.md`
- `docs/PJSIP_macOS.md`
- `docs/PJSIP_Linux.md`
- `docs/PJSIP_Windows.md`
