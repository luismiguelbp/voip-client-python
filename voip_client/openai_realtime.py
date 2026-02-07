"""
OpenAI Realtime audio bridge.

Thin wrapper around the OpenAI Realtime WebSocket API that:
- Runs an asyncio event loop in a background thread
- Sends raw PCM audio chunks to OpenAI as input
- Receives raw PCM audio chunks from OpenAI as output

This is intentionally minimal and synchronous from the caller's perspective.
"""

import asyncio
import base64
import contextlib
import json
import os
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

import websockets


class OpenAIRealtimeBridge:
    """
    Simple bridge for full-duplex audio with OpenAI Realtime.

    Usage (from synchronous code):

        bridge = OpenAIRealtimeBridge(system_message="...", voice="alloy")
        bridge.start()
        bridge.send_pcm(some_bytes)
        data = bridge.recv_pcm(timeout=0.1)
        ...
        bridge.stop()

    By default, when server VAD reports a speech stop, the bridge commits the
    audio buffer and sends a response.create so the AI replies automatically.
    """

    def __init__(
        self,
        system_message: str,
        voice: str = "alloy",
        model: str = "gpt-realtime",
        sample_rate: int = 8000,
        auto_response: bool = True,
        silence_duration_ms: int = 1000,
        vad_threshold: float = 0.5,
        prefix_padding_ms: int = 300,
    ) -> None:
        self._system_message = system_message
        self._voice = voice
        # Allow overriding the Realtime model via env
        env_model = os.getenv("OPENAI_RT_MODEL", "").strip()
        self._model = env_model or model
        self._sample_rate = sample_rate
        self._auto_response = auto_response
        # VAD tuning for clearer turn-taking (phone-bot style)
        self._silence_duration_ms = silence_duration_ms
        self._vad_threshold = vad_threshold
        self._prefix_padding_ms = prefix_padding_ms
        # First try OS environment, then fall back to loading a local .env
        self._openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self._openai_api_key:
            self._load_env_from_project_root()
            self._openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self._openai_api_key:
            raise ValueError("Missing OPENAI_API_KEY in environment.")

        # Queues for PCM frames (bytes)
        self._input_q: "Queue[bytes]" = Queue(maxsize=100)
        self._output_q: "Queue[bytes]" = Queue(maxsize=100)

        # Thread / loop management
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._session_ready = threading.Event()  # Set when session.updated is received
        self._response_done = threading.Event()  # Set when response.done is received
        self._response_in_progress = False  # True between response.created and response.done

    # Public API ---------------------------------------------------------

    def start(self) -> None:
        """Start background thread and connect to OpenAI."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._session_ready.clear()
        self._response_done.clear()
        self._response_in_progress = False
        self._thread = threading.Thread(
            target=self._run_loop, name="OpenAIRealtimeBridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal shutdown and wait for thread to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def send_pcm(self, data: bytes) -> None:
        """
        Enqueue a chunk of PCM16 mono audio for OpenAI.

        Non-blocking: drops frames if the queue is full to avoid deadlock.
        """
        if not data:
            return
        try:
            self._input_q.put_nowait(data)
        except Exception:
            # Drop if full
            pass

    def recv_pcm(self, timeout: float = 0.0) -> Optional[bytes]:
        """
        Retrieve a chunk of PCM16 mono audio from OpenAI, if available.

        Returns None on timeout.
        """
        try:
            return self._output_q.get(timeout=timeout)
        except Empty:
            return None

    # Internal helpers ---------------------------------------------------

    @staticmethod
    def _load_env_from_project_root() -> None:
        """
        Best-effort load of a .env file from the project root.

        This mirrors the simple loader used elsewhere in the project:
        - Only lines of the form KEY=VALUE
        - Skips comments and empty lines
        - Does not overwrite existing environment variables
        """
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if not env_path.exists():
            return
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key not in os.environ:
                os.environ[key] = value

    def _run_loop(self) -> None:
        """Background thread entry: create and run asyncio loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # pragma: no cover - best-effort logging
            print(f"[OpenAIRealtimeBridge] Loop error: {exc}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        """Connect to OpenAI Realtime and proxy audio both ways."""
        url = f"wss://api.openai.com/v1/realtime?model={self._model}"
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}

        try:
            print(f"[OpenAIRealtimeBridge] Connecting to {url}")
            # websockets.connect in this project expects "additional_headers",
            # matching the official OpenAI Realtime Python examples.
            async with websockets.connect(url, additional_headers=headers) as ws:
                print("[OpenAIRealtimeBridge] WebSocket connected")
                await self._initialize_session(ws)
                # Give the API a moment to process the session.update
                await asyncio.sleep(0.1)
                # Optionally have the AI speak first:
                # await self._send_initial_message(ws)

                sender = asyncio.create_task(self._send_audio(ws))
                receiver = asyncio.create_task(self._receive_audio(ws))

                try:
                    while not self._stop_event.is_set():
                        await asyncio.sleep(0.05)
                        # Check if tasks completed with errors
                        if sender.done():
                            try:
                                await sender
                            except Exception as exc:
                                print(f"[OpenAIRealtimeBridge] Sender task error: {exc}")
                                import traceback
                                traceback.print_exc()
                        if receiver.done():
                            try:
                                await receiver
                            except Exception as exc:
                                print(f"[OpenAIRealtimeBridge] Receiver task error: {exc}")
                                import traceback
                                traceback.print_exc()
                finally:
                    sender.cancel()
                    receiver.cancel()
                    with contextlib.suppress(Exception):
                        await ws.close()
                    print("[OpenAIRealtimeBridge] WebSocket closed")
        except websockets.exceptions.InvalidStatusCode as exc:
            print(f"[OpenAIRealtimeBridge] WebSocket connection failed: HTTP {exc.status_code}")
            if exc.status_code == 401:
                print("[OpenAIRealtimeBridge] Authentication failed - check OPENAI_API_KEY")
            elif exc.status_code == 404:
                print(f"[OpenAIRealtimeBridge] Model not found: {self._model}")
        except Exception as exc:
            print(f"[OpenAIRealtimeBridge] Connection error: {exc}")
            import traceback
            traceback.print_exc()

    async def _initialize_session(self, ws: websockets.WebSocketClientProtocol) -> None:
        """
        Configure the Realtime session for audio in/out.

        Mirrors the Twilio Python example's initialize_session, but uses
        linear PCM16 instead of PCMU for simplicity.
        """
        # NOTE: The Realtime API format type must be 'audio/pcm' (not 'audio/pcm16').
        # The format.rate parameter is required and specifies the sample rate in Hz.
        session_update = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self._model,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": self._sample_rate,
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "silence_duration_ms": self._silence_duration_ms,
                            "threshold": self._vad_threshold,
                            "prefix_padding_ms": self._prefix_padding_ms,
                        },
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": self._sample_rate,
                        },
                        "voice": self._voice,
                    },
                },
                "instructions": self._system_message,
            },
        }
        print(f"[OpenAIRealtimeBridge] Sending session.update (model={self._model}, sample_rate={self._sample_rate})")
        session_update_json = json.dumps(session_update)
        print(f"[OpenAIRealtimeBridge] Session update payload: {session_update_json[:200]}...")  # Log first 200 chars
        try:
            await ws.send(session_update_json)
        except Exception as exc:
            print(f"[OpenAIRealtimeBridge] Failed to send session.update: {exc}")
            import traceback
            traceback.print_exc()
            raise

    async def _request_response(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Ask the model to respond. With server VAD, the server has already committed on speech_stopped."""
        if self._response_in_progress:
            return
        try:
            await ws.send(json.dumps({"type": "response.create"}))
        except Exception as exc:
            print(f"[OpenAIRealtimeBridge] response.create error: {exc}")

    async def _send_audio(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Read PCM from input queue and send to OpenAI."""
        # Wait for session to be ready before sending audio
        if not self._session_ready.wait(timeout=5.0):
            print("[OpenAIRealtimeBridge] Warning: Session not ready after 5 seconds, starting to send audio anyway")
        
        try:
            while True:
                if self._stop_event.is_set():
                    return
                try:
                    chunk = self._input_q.get_nowait()
                except Empty:
                    # Yield so the receiver task can run (session.updated, audio deltas).
                    await asyncio.sleep(0.02)
                    continue

                if not chunk:
                    continue

                # OpenAI expects base64-encoded audio payloads
                b64 = base64.b64encode(chunk).decode("ascii")
                msg = {
                    "type": "input_audio_buffer.append",
                    "audio": b64,
                }
                try:
                    await ws.send(json.dumps(msg))
                except websockets.exceptions.ConnectionClosed:
                    print("[OpenAIRealtimeBridge] WebSocket closed while sending audio")
                    return
                except Exception as exc:
                    print(f"[OpenAIRealtimeBridge] Error sending audio: {exc}")
                    import traceback
                    traceback.print_exc()
                    return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[OpenAIRealtimeBridge] _send_audio error: {exc}")
            import traceback
            traceback.print_exc()

    async def _receive_audio(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Receive audio deltas from OpenAI and push PCM into output queue."""
        try:
            event_count = 0
            print("[OpenAIRealtimeBridge] Starting receive loop")
            async for raw in ws:
                try:
                    event = json.loads(raw)
                    event_count += 1
                except Exception as exc:
                    print(f"[OpenAIRealtimeBridge] Failed to parse event: {exc}")
                    print(f"[OpenAIRealtimeBridge] Raw event data: {raw[:200]}")
                    continue

                etype = event.get("type")
                
                # Log all events for debugging (first 20 events)
                if event_count <= 20:
                    print(f"[OpenAIRealtimeBridge] Event #{event_count}: {etype}")
                    if etype == "error":
                        print(f"[OpenAIRealtimeBridge] Full error event: {event}")
                
                # Log session confirmation
                if etype == "session.updated":
                    print("[OpenAIRealtimeBridge] Session confirmed, ready to receive audio")
                    self._session_ready.set()
                # Log errors
                elif etype == "error":
                    error_msg = event.get("error", {})
                    print(f"[OpenAIRealtimeBridge] API Error: {error_msg}")
                    import traceback
                    traceback.print_exc()
                # Log speech detection events for debugging
                elif etype == "input_audio_buffer.speech_started":
                    print("[OpenAIRealtimeBridge] Speech detected")
                elif etype == "input_audio_buffer.speech_stopped":
                    print("[OpenAIRealtimeBridge] Speech stopped, requesting response")
                    if self._auto_response:
                        await self._request_response(ws)
                    continue
                elif etype == "response.output_audio.delta" and "delta" in event:
                    try:
                        # delta is base64-encoded PCM16
                        pcm = base64.b64decode(event["delta"])
                        try:
                            self._output_q.put_nowait(pcm)
                            # Log first few audio chunks for debugging
                            if event_count <= 25:
                                print(f"[OpenAIRealtimeBridge] Received audio delta: {len(pcm)} bytes")
                        except Exception as exc:
                            # Drop if full
                            print(f"[OpenAIRealtimeBridge] Output queue full, dropping audio: {exc}")
                    except Exception as exc:
                        print(f"[OpenAIRealtimeBridge] Failed to decode audio delta: {exc}")
                        continue
                # Log response events and track in-progress
                elif etype == "response.created":
                    self._response_in_progress = True
                    print("[OpenAIRealtimeBridge] Response created")
                elif etype == "response.done":
                    print("[OpenAIRealtimeBridge] Response done")
                    self._response_done.set()
                    self._response_in_progress = False
                elif etype == "response.output_audio.done":
                    print("[OpenAIRealtimeBridge] Response audio done")
                    self._response_done.set()
                    self._response_in_progress = False
                elif etype == "response.audio_transcript.delta":
                    transcript_delta = event.get("delta", "")
                    if transcript_delta:
                        print(f"[OpenAIRealtimeBridge] Transcript: {transcript_delta}", end="", flush=True)
                elif etype == "response.audio_transcript.done":
                    print()  # New line after transcript
        except asyncio.CancelledError:
            return
        except websockets.exceptions.ConnectionClosed as exc:
            print(f"[OpenAIRealtimeBridge] WebSocket connection closed: code={exc.code}, reason={exc.reason}")
            if not self._session_ready.is_set():
                print("[OpenAIRealtimeBridge] Warning: Connection closed before session was ready!")
        except Exception as exc:
            print(f"[OpenAIRealtimeBridge] _receive_audio error: {exc}")
            import traceback
            traceback.print_exc()

