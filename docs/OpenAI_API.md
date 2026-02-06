## OpenAI AI Assistant Integration

This project includes an integration with OpenAI APIs to let a phone call talk to an AI Assistant.

### Architecture

The AI Assistant uses a pipeline approach:

1. **Speech-to-Text (STT)**: Whisper API transcribes caller speech
2. **Reasoning**: Chat Completions API generates responses
3. **Text-to-Speech (TTS)**: OpenAI TTS API converts responses to audio

```
Phone User <--[SIP Audio]--> PJSIP <--[PCM Audio]--> WhisperAssistantBridge
                                                         |
                                                         v
                                                  [Energy-based VAD]
                                                         |
                                                         v
                                                  [Whisper API (STT)]
                                                         |
                                                         v
                                                  [Chat Completions]
                                                         |
                                                         v
                                                  [OpenAI TTS]
                                                         |
                                                         v
                                                   [PCM Audio]
```

### Components

- `voip_client/whisper_assistant.py`
  - `WhisperAssistantBridge`:
    - Runs an asyncio loop in a background thread.
    - Uses energy-based VAD for turn detection.
    - Buffers audio during speech, triggers processing on silence.
    - Sequential API calls: Whisper -> Chat Completions -> TTS.
    - Maintains conversation history for multi-turn context.
    - Exposes a simple sync API:
      - `start()` / `stop()`
      - `send_pcm(data: bytes)` - enqueue PCM16 audio for processing.
      - `recv_pcm(timeout: float) -> Optional[bytes]` - read PCM16 audio from TTS.

- `voip_client/app_ai_bot_call.py`
  - `AiBotCall(BaseVoipCall)`:
    - Uses the existing SIP session/account (`VoipSession`, `VoipAccount`).
    - On media active, starts a `WhisperAssistantBridge` and bridges audio between PJSIP and the AI.
    - Behaves as an **AI Bot**: listens to the caller and maintains a natural back-and-forth conversation until the call ends.
    - Exposes `--silence-duration` CLI flag to control turn-taking (how long to wait after silence before responding).
    - Exposes `--model` CLI flag to choose the chat model (default: `gpt-4o`).

- `voip_client/app_ai_rt_call.py`
  - `AiRtCall(BaseVoipCall)`:
    - Uses the existing SIP session/account (`VoipSession`, `VoipAccount`).
    - On media active, creates an `OpenAIRealtimeBridge` and bridges audio between PJSIP and OpenAI Realtime.
    - Full-duplex audio over WebSocket with server-side VAD for turn detection.
    - Lower latency than the Whisper pipeline.
    - Exposes `--silence-duration`, `--vad-threshold`, and `--prefix-padding` CLI flags for VAD tuning.

### Environment variables

In addition to the SIP variables in `.env` (`SIP_DOMAIN`, `SIP_USERNAME`, `SIP_PASSWORD`, etc.), the AI Assistant integration uses these variables:

- `OPENAI_API_KEY` - your OpenAI API key (required)
- `OPENAI_MODEL` - chat model for reasoning, e.g. `gpt-4o` (default: `gpt-4o`)
- `OPENAI_TTS_MODEL` - TTS model, e.g. `tts-1` or `tts-1-hd` (default: `tts-1`)
- `OPENAI_TTS_VOICE` - TTS voice (default: `alloy`, can override via CLI)

Set them in your shell, for example:

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4o"
export OPENAI_TTS_MODEL="tts-1"
```

or add them to `.env` (see `.env.example`) and ensure it is loaded before running the scripts.

### Usage: AI Bot call (Whisper pipeline)

From the project root, with your virtualenv active and PJSIP set up:

```bash
python -m voip_client.app_ai_bot_call <phone_number> \
  --reg-timeout 15 \
  --system-message "You are a helpful AI assistant on a phone call. The caller may speak in English or Spanish. First, detect the caller's language and always reply in that same language. Keep responses short and easy to understand over the phone." \
  --voice alloy \
  --model gpt-4o \
  --silence-duration 1000
```

### Usage: AI Realtime call

```bash
python -m voip_client.app_ai_rt_call <phone_number> \
  --reg-timeout 15 \
  --system-message "You are a helpful AI assistant on a phone call. The caller may speak in English or Spanish. First, detect the caller's language and always reply in that same language. Keep responses short and easy to understand over the phone." \
  --voice alloy \
  --model gpt-realtime \
  --silence-duration 1000
```

- Both scripts register to your SIP server.
- Place a call to `<phone_number>` using the same SIP configuration as other `app_*` tools.
- Once the call media is active, they attach the AI bridge and let the AI **listen and respond in a natural conversation** until the call is ended.

### Tuning turn-taking

The `--silence-duration` flag controls how long (in milliseconds) the assistant waits after the caller stops speaking before responding:

- **Lower values** (e.g. 500): Assistant responds quickly, may interrupt if the caller pauses briefly.
- **Higher values** (e.g. 1500): Assistant waits longer, gives the caller more time to finish speaking.
- **Default**: 1000ms, a reasonable balance for phone conversations.

Example with longer pause (bot):

```bash
python -m voip_client.app_ai_bot_call <phone_number> --silence-duration 1500
```

### Notes and limitations

- Audio is configured as **PCM16** at an 8 kHz sample rate, which matches typical telephony bandwidth.
- The bridge uses energy-based VAD for turn detection. The silence threshold is set to -40 dB by default.
- TTS audio from OpenAI (24 kHz) is resampled to 8 kHz for telephony (anti-aliasing used for downsampling).
- Latency is higher than the Realtime API due to sequential API calls (STT -> Chat -> TTS), but cost is lower and you can use any chat model.
- Use `--save-recordings` with `app_ai_bot_call` to save WAVs and transcripts; see **docs/AI_Assistant.md**.
- Conversation history is maintained in memory for multi-turn context. Long conversations may need history management to avoid hitting token limits.
