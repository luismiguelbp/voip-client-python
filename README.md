# voip-client-python

VoIP Client using Python and PJSIP.

## Requirements

- Python 3.9+
- PJSIP with Python bindings (pjsua2)

## Setup

### Install pjsua2 Python bindings

1. Build PJSIP from source (see `docs/PJSIP.md` for platform-specific instructions)

2. Run the setup script with `PJPROJECT_DIR` pointing to your pjproject source:

```bash
cd voip-client-python
PJPROJECT_DIR=/path/to/pjproject ./scripts/setup_pjsua2.sh
```

This creates a virtual environment and installs the pjsua2 bindings.

3. Activate the virtual environment:

```bash
source .venv/bin/activate
```

## Project Structure

```
voip_client/          # Main package
  pjsip_test.py       # PJSIP install/basic test
  pjsip_test_voip.py  # VoIPstudio registration test
  audio_test.py       # Microphone/speaker test with recording
```

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

3. Run the registration test:

```bash
python -m voip_client.pjsip_test_voip
```

### Audio device test (microphone/speakers)

Test your microphone and speakers with a loopback (hear yourself) and save a recording:

```bash
python -m voip_client.audio_test
```

Recordings are saved to `recordings/audio_test_YYYYMMDD_HHMMSS.wav` by default.

Options:
- `--duration`, `-d`: Test duration in seconds (default: 10)
- `--output`, `-o`: Custom output WAV file path

Example with custom duration:

```bash
python -m voip_client.audio_test --duration 30
```

## Documentation

- `docs/VoIPstudio.md` - VoIPstudio-specific SIP settings
- `docs/PJSIP.md` - Building PJSIP and platform-specific guides
