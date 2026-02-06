#!/usr/bin/env python3
"""
Small script to verify basic connectivity with the OpenAI API.

It:
- Loads OPENAI_API_KEY from the environment or a local .env file
- Sends a simple chat completion request
- Prints the model's reply
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict
from urllib import error, request

from .openai_realtime import OpenAIRealtimeBridge


def _load_env_from_project_root() -> None:
    """
    Best-effort load of a .env file from the project root.

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


def _get_openai_api_key() -> str:
    """Fetch OPENAI_API_KEY from env, trying .env if needed."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        _load_env_from_project_root()
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: OPENAI_API_KEY is not set in the environment or .env file.",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def send_chat_message(message: str) -> str:
    """
    Send a single user message to OpenAI's chat completions API
    and return the assistant's reply as plain text.
    """
    api_key = _get_openai_api_key()
    url = "https://api.openai.com/v1/chat/completions"

    body: Dict[str, Any] = {
        "model": os.getenv("OPENAI_MODEL", "gpt-5.2"),
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": message},
        ],
    }

    data = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    req = request.Request(url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        print(f"HTTP error from OpenAI: {exc.code} {exc.reason}", file=sys.stderr)
        try:
            detail = exc.read().decode("utf-8")
            print(detail, file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except error.URLError as exc:
        print(f"Connection error when calling OpenAI: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        payload = json.loads(raw)
        return payload["choices"][0]["message"]["content"]
    except Exception:
        print("Unexpected response payload from OpenAI:", file=sys.stderr)
        print(raw, file=sys.stderr)
        sys.exit(1)


def test_realtime() -> None:
    """
    Minimal OpenAI Realtime connectivity test.

    It:
    - Creates an OpenAIRealtimeBridge
    - Connects to the Realtime WebSocket API
    - Waits briefly
    - Closes the connection
    """
    print("Testing OpenAI Realtime websocket...")

    try:
        bridge = OpenAIRealtimeBridge(
            system_message="You are a helpful voice assistant for test calls."
        )
        bridge.start()
        # Give the background loop a moment to connect and send session.update.
        time.sleep(3.0)
        bridge.stop()
    except Exception as exc:
        print(f"Realtime test failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Realtime websocket test completed without error.")


def main() -> None:
    print("=== Chat completions test ===")
    user_message = "Say hello and tell me the current year in one short sentence."
    reply = send_chat_message(user_message)

    print()
    print("User:", user_message)
    print("Assistant:", reply)

    print()
    print("=== Realtime API test ===")
    test_realtime()


if __name__ == "__main__":
    main()

