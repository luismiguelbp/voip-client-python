# voip-client-python

VoIP Client using Python and PJSIP.

## Requirements

- Python 3.9+
- PJSIP with Python bindings (pjsua2)

## Setup

### Install pjsua2 Python bindings

1. Build PJSIP from source (see `docs/PJSIP.md` for platform-specific instructions).

2. From the project root, run the setup script with `PJPROJECT_DIR` pointing to your pjproject source:

```bash
cd /path/to/voip-client-python
PJPROJECT_DIR=/path/to/pjproject ./scripts/setup_pjsua2.sh
```

This creates a virtual environment and installs the pjsua2 bindings.

3. Activate the virtual environment (from the project root):

```bash
source .venv/bin/activate
```

### Credentials / Security

Never commit `.env`. Copy from `.env.example` and fill in real values only locally. `.env` is listed in `.gitignore`.

Logs are written to `logs/`, recordings to `recordings/`; both directories are local and gitignored.

All commands below assume you are in the project root with the virtual environment activated.

## Project Structure

```
voip_client/            # Main package
  voip_common.py        # Shared session, account, and call base (SIP/voip_*/app_*)
  pjsip_common.py       # PJSIP endpoint helper for pjsip_* tests (no SIP)
  pjsip_test.py         # PJSIP install/basic test
  pjsip_test_voip.py    # VoIPstudio registration test
  pjsip_test_audio.py   # PJSIP audio test: devices, loopback, record (no SIP)
  app_custom_call.py    # Custom outbound call: play WAV, record, hang up on Enter
  app_echo_call.py      # Echo call to any number (hear yourself)
  voip_test_call.py     # Test Call (#123): connect, optional record, Enter to hang up
  voip_echo_test.py     # Echo Test (#124): hear echo, optional record/duration
  voip_dtmf_test.py     # DTMF Test (#125): send DTMF digits, then hang up
```

### Naming conventions

- **pjsip_*** – PJSIP tests (no SIP account, or registration only): `pjsip_test`, `pjsip_test_voip`, `pjsip_test_audio`. Shared helper: `pjsip_common.PjsipEndpoint`, `PjsipAudioTest`.
- **voip_*** – Provider test scripts (VoIPstudio test numbers #123, #124, #125). Use `voip_common` (VoipSession, BaseVoipCall). Call classes: `VoipTestCall`, `VoipEchoTestCall`, `VoipDtmfTestCall`.
- **app_*** – Application scripts (outbound calls to any number). Use `voip_common`. Call classes: `CustomOutboundCall`, `AppEchoCall`.

## Usage

### Basic install test

```bash
python -m voip_client.pjsip_test
```

### VoIPstudio registration test

1. Create a local `.env` file:

```bash
cp .env.example .env
```

2. Fill in your VoIPstudio SIP credentials in `.env`:

- `SIP_DOMAIN`
- `SIP_USERNAME`
- `SIP_PASSWORD`

Optional overrides:

- `SIP_AUTH_USERNAME`
- `SIP_TRANSPORT` (udp, tcp, tls)
- `SIP_PORT`
- `SIP_PROXY`
- `SIP_REG_TIMEOUT` (seconds, default 15)
- `SIP_TEST_CALL_EXTENSION` (default 123 for Test Call)
- `SIP_ECHO_EXTENSION` (default 124 for Echo Test)
- `SIP_DTMF_TEST_EXTENSION` (default 125 for DTMF Test)

3. Run the registration test:

```bash
python -m voip_client.pjsip_test_voip
```

### PJSIP audio test (microphone/speakers)

Test PJSIP audio: list devices, loopback (hear yourself), and save a recording. No SIP or account.

```bash
python -m voip_client.pjsip_test_audio
```

Recordings are saved to `recordings/pjsip_test_audio_YYYYMMDD_HHMMSS.wav` by default.

Options:
- `--duration`, `-d`: Test duration in seconds (default: 5)
- `--output`, `-o`: Custom output WAV file path

Example with custom duration:

```bash
python -m voip_client.pjsip_test_audio --duration 30
```

### Custom outbound call (play WAV, record, hang up on Enter)

Requires the same `.env` as the VoIPstudio registration test. Calls a phone number (or extension), plays an optional WAV when the call is answered, records the call, and hangs up when you press Enter.

```bash
python -m voip_client.app_custom_call <phone_number> [--audio WAV_PATH] [--output WAV_PATH]
```

- `phone_number`: Destination number or extension (e.g. `0035123456789` or an extension).
- `--audio`, `-a`: Path to a WAV file to play to the remote party when the call is answered.
- `--output`, `-o`: Path for the call recording (default: `recordings/call_YYYYMMDD_HHMMSS.wav`).
- `--reg-timeout`: Registration timeout in seconds (default: 15).

Example:

```bash
python -m voip_client.app_custom_call 0035123456789 --audio message.wav
```

When the call is connected, the script prints "Call connected. Press Enter to end call and save recording." Press Enter to hang up; the recording is saved automatically.

### VoIPstudio test cases (Test Call, Echo Test, DTMF Test)

VoIPstudio provides test numbers: **#123** (Test Call), **#124** (Echo Test), **#125** (DTMF Test). Default destinations are 123, 124, 125; override with `SIP_TEST_CALL_EXTENSION`, `SIP_ECHO_EXTENSION`, or `SIP_DTMF_TEST_EXTENSION` in `.env` or by passing a destination argument.

**Test Call (#123):** Call test number, connect audio, optional recording, Enter to hang up.

```bash
python -m voip_client.voip_test_call [destination] [--output WAV_PATH]
```

**Echo Test (#124):** Call echo number, hear your voice echoed back, optional recording and/or `--duration` to auto-hangup.

```bash
python -m voip_client.voip_echo_test [destination] [--duration SECS] [--output WAV_PATH]
```

**DTMF Test (#125):** Call DTMF test number, send a sequence of digits, then hang up.

```bash
python -m voip_client.voip_dtmf_test [destination] [--digits "1234567890#*"] [--digit-delay-ms MS]
```

- `--reg-timeout`: Registration timeout in seconds (default: 15) for all of the above.

## Development

Run from the project root with the virtual environment activated (see Setup above).

## Documentation

- **docs/PJSIP.md** – PJSIP overview and building the Python bindings
- **docs/PJSIP_macOS.md**, **docs/PJSIP_Linux.md**, **docs/PJSIP_Windows.md** – Platform-specific PJSIP install
- **docs/VoIPstudio.md** – VoIPstudio SIP settings and mapping to PJSIP
