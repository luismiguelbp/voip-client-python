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

Logs are written to `logs/`, recordings to `recordings/`, and temporary files to `tmp/`; these directories are local and gitignored.

All commands below assume you are in the project root with the virtual environment activated.

## Project Structure

```
voip_client/            # Main package
  voip_common.py        # Shared session, account, and call base (SIP/voip_*/app_*)
  pjsip_common.py       # PJSIP endpoint helper for pjsip_* tests (no SIP)
  pjsip_test.py         # PJSIP install/basic test
  pjsip_test_voip.py    # VoIPstudio registration test
  pjsip_test_audio.py   # PJSIP audio test: devices, loopback, record (no SIP)
  voip_test_call.py     # Test Call (#123): connect, record to recordings/, Enter to hang up
  voip_echo_test.py     # Echo Test (#124): hear echo, record to recordings/, optional --duration
  voip_dtmf_test.py     # DTMF Test (#125): send DTMF digits, then hang up
  app_phone_call.py     # Normal outbound call: two-way audio, record, hang up on Enter
  app_echo_call.py      # Echo call to any number (hear yourself)
  app_ai_chatbot_call.py    # Outbound call with AI bot (Whisper STT + Chat Completions + TTS pipeline)
  app_ai_realtime_call.py   # Outbound call with AI assistant via OpenAI Realtime API (full-duplex WebSocket)
```

### Naming conventions

- **pjsip_*** – PJSIP tests (no SIP account, or registration only): `pjsip_test`, `pjsip_test_voip`, `pjsip_test_audio`. Shared helper: `pjsip_common.PjsipEndpoint`, `PjsipAudioTest`.
- **voip_*** – Provider test scripts (VoIPstudio test numbers #123, #124, #125). Use `voip_common` (VoipSession, BaseVoipCall). Call classes: `VoipTestCall`, `VoipEchoTestCall`, `VoipDtmfTestCall`.
- **app_*** – Application scripts (outbound calls to any number). Use `voip_common`. Call classes: `PhoneCall`, `AppEchoCall`, `AiBotCall`, `AiRtCall`.

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

Recordings are saved to `recordings/pjsip_test_audio_YYYYMMDD_HHMMSS.wav`.

Options:
- `--duration`, `-d`: Test duration in seconds (default: 5)

Example with custom duration:

```bash
python -m voip_client.pjsip_test_audio --duration 30
```

### Outbound phone call (two-way audio + recording)

Requires the same `.env` as the VoIPstudio registration test. Calls a phone number (or extension), connects normal two-way audio, records the conversation, and hangs up when you press Enter.

```bash
python -m voip_client.app_phone_call <phone_number>
```

- `phone_number`: Destination number or extension (e.g. `0035123456789` or an extension).
- Recording saved to `recordings/app_phone_call_YYYYMMDD_HHMMSS.wav`.
- `--reg-timeout`: Registration timeout in seconds (default: 15).
- `--debug`: Write event log to `recordings/app_phone_call_debug_<ts>.log` for debugging.

When the call is connected, the script prints "Call connected. Press Enter to end call and save recording." Press Enter to hang up; the recording is saved automatically.

### Echo call (outbound)

Call any number and hear yourself (mic routed to speaker). Same `.env` as registration test. Recording saved to `recordings/app_echo_call_YYYYMMDD_HHMMSS.wav`. Optional `--duration` to auto-hangup (default: 5s).

```bash
python -m voip_client.app_echo_call <phone_number> [--duration SECS]
```

- `--duration`, `-d`: Auto-hangup after N seconds (default: 5).
- `--debug`: Write event log to `recordings/app_echo_call_debug_<ts>.log` for debugging.

### Outbound phone call with AI assistant

Requires the same `.env` as the VoIPstudio registration test **plus** OpenAI environment variables:

- `OPENAI_API_KEY` – your OpenAI API key (required)
- `OPENAI_MODEL` – chat model for the bot pipeline (default: `gpt-4o`)
- `OPENAI_TTS_MODEL` – TTS model for the bot pipeline (default: `tts-1`)
- `OPENAI_TTS_VOICE` – TTS voice override for the bot pipeline
- `OPENAI_RT_MODEL` – model used for Realtime audio in `OpenAIRealtimeBridge` (default: `gpt-realtime`)

These should be defined in your local `.env`, based on `.env.example`.

There are two AI call modes:

#### AI Bot (Whisper STT + Chat Completions + TTS)

Uses a pipeline approach: caller audio is transcribed with Whisper, processed by Chat Completions, and the response is converted back to speech with OpenAI TTS. Higher latency but supports any chat model and is more cost-effective.

```bash
python -m voip_client.app_ai_chatbot_call <phone_number> [--reg-timeout SECONDS] [--system-message TEXT] [--voice VOICE_NAME] [--model MODEL] [--silence-duration MS]
```

- `phone_number`: Destination number or extension.
- `--reg-timeout`: Registration timeout in seconds (default: 15).
- `--system-message`: System/prompt instructions for the AI assistant.
- `--voice`: OpenAI TTS voice name (default: `alloy`).
- `--model`: Chat model to use (default: `gpt-4o`).
- `--silence-duration`: How long (ms) to wait after silence before responding (default: 1000).
- `--debug`: Write event log to `recordings/app_ai_chatbot_call_debug_<ts>.log` for debugging call flow.

#### AI Realtime (OpenAI Realtime API)

Uses the OpenAI Realtime API for full-duplex audio over WebSocket. Lower latency with server-side VAD for turn detection.

```bash
python -m voip_client.app_ai_realtime_call <phone_number> [--reg-timeout SECONDS] [--system-message TEXT] [--voice VOICE_NAME] [--model MODEL] [--silence-duration MS]
```

- `phone_number`: Destination number or extension.
- `--reg-timeout`: Registration timeout in seconds (default: 15).
- `--system-message`: System/prompt instructions for the AI assistant.
- `--voice`: OpenAI voice name (default: `alloy`).
- `--model`: Realtime model to use (default: `gpt-realtime`, override with `OPENAI_RT_MODEL` env).
- `--silence-duration`: How long (ms) server VAD waits after silence (default: 1000).
- `--vad-threshold`: Server VAD activation threshold, 0.0-1.0 (default: 0.5).
- `--prefix-padding`: Audio to include before detected speech, in ms (default: 300).
- `--debug`: Write event log to `recordings/app_ai_realtime_call_debug_<ts>.log` for debugging (see docs/OpenAI_API.md).

### OpenAI connectivity test script

To quickly verify that your OpenAI credentials and network connectivity work, use the small test helper:

```bash
python -m voip_client.openai_test
```

This script:

- Loads `OPENAI_API_KEY` and `OPENAI_MODEL` (default: `gpt-5.2`) from the environment / `.env`
- Sends a short test prompt to the chat completions API
- Prints the assistant reply or a detailed error (for example, insufficient quota)

### VoIPstudio test cases (Test Call, Echo Test, DTMF Test)

VoIPstudio provides test numbers: **#123** (Test Call), **#124** (Echo Test), **#125** (DTMF Test). Default destinations are 123, 124, 125; override with `SIP_TEST_CALL_EXTENSION`, `SIP_ECHO_EXTENSION`, or `SIP_DTMF_TEST_EXTENSION` in `.env` or by passing a destination argument.

**Test Call (#123):** Call test number, connect audio, recording to `recordings/`, Enter to hang up.

```bash
python -m voip_client.voip_test_call [destination]
```

**Echo Test (#124):** Call echo number, hear your voice echoed back, recording to `recordings/`, optional `--duration` to auto-hangup (default: 5s).

```bash
python -m voip_client.voip_echo_test [destination] [--duration SECS]
```

**DTMF Test (#125):** Call DTMF test number, send a sequence of digits, then hang up.

```bash
python -m voip_client.voip_dtmf_test [destination] [--digits "1234567890#*"] [--digit-delay-ms MS]
```

- `--reg-timeout`: Registration timeout in seconds (default: 15) for all of the above.

## Test checklist

Use this checklist to track which scripts have been manually tested after changes. Order: pjsip, then voip, then app scripts. Commands below use default options (no `--debug`); test scripts (pjsip_*, voip_*_test) print trace output by default. For troubleshooting app scripts, use `--debug` to write event logs to `recordings/`.

- [ ] `python -m voip_client.pjsip_test` – PJSIP install/basic test
- [ ] `python -m voip_client.pjsip_test_voip` – VoIPstudio registration test
- [ ] `python -m voip_client.pjsip_test_audio` – PJSIP audio (devices/loopback/record) test
- [ ] `python -m voip_client.voip_test_call [destination]` – VoIPstudio Test Call (#123)
- [ ] `python -m voip_client.voip_echo_test [destination]` – VoIPstudio Echo Test (#124)
- [ ] `python -m voip_client.voip_dtmf_test [destination]` – VoIPstudio DTMF Test (#125)
- [ ] `python -m voip_client.openai_test` – OpenAI connectivity test
- [ ] `python -m voip_client.app_phone_call <phone_number>` – Outbound phone call (two-way audio + recording)
- [ ] `python -m voip_client.app_echo_call <phone_number>` – Echo call application
- [ ] `python -m voip_client.app_ai_chatbot_call <phone_number>` – Outbound call with AI bot (Whisper + Chat + TTS)
- [ ] `python -m voip_client.app_ai_realtime_call <phone_number>` – Outbound call with Realtime AI assistant

## Development

Run from the project root with the virtual environment activated (see Setup above).

## Documentation

- **docs/PJSIP.md** – PJSIP overview and building the Python bindings
- **docs/PJSIP_macOS.md**, **docs/PJSIP_Linux.md**, **docs/PJSIP_Windows.md** – Platform-specific PJSIP install
- **docs/VoIPstudio.md** – VoIPstudio SIP settings and mapping to PJSIP
- **docs/OpenAI_API.md** – AI Assistant architecture (Whisper pipeline and Realtime API), env vars, usage
- **docs/AI_Assistant.md** – AI bot call: recording, transcription, sample rate, testing
