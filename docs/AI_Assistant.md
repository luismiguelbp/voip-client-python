# AI ChatBot Call – Recording and Transcription

When using `--save-recordings`, files are written under `recordings/app_ai_chatbot_call_<timestamp>/`.

## Behavior

- **Transcriptions**: User and assistant text saved as `transcript_{NNN}_user.txt`, `transcript_{NNN}_assistant.txt`; full conversation in `full_transcript.txt` on call end (UTF-8).
- **Response audio**: Resampling uses anti-aliasing; responses use the call stream sample rate (e.g. 8 kHz), not the recorder file rate.
- **Sample rate**: Call stream rate is used consistently for bridge and processing; recorder file rate is not used to change it.
- **PJ_EINVAL**: Non-critical PJSIP warnings when removing ports; no impact on behavior.

## Relevant files

- `voip_client/whisper_assistant.py`: Transcription saving; `_resample_pcm()` with anti-aliasing.
- `voip_client/app_ai_chatbot_call.py`: Sample rate handling, full transcript on cleanup, sample rate logging.

## Testing

1. Run with `--save-recordings`.
2. Confirm `transcript_000_user.txt`, `transcript_000_assistant.txt`, `full_transcript.txt`.
3. Check response and segment WAVs are at call rate (e.g. 8 kHz).

## Open points

- If segments sound bad: check audio buffer handling.
- If response quality is poor: try better TTS or TTS at closer sample rates.
- Port cleanup warnings: optional improvements with delays or sync for port removal.

