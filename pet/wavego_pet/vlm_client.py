"""VLM client — llama-server's OpenAI-compatible endpoint.

One scene question per frame, STRICTLY structured: the model must answer
with a tiny JSON object; anything unparseable degrades to "nothing seen"
(the pet just stays calm) rather than inventing behavior. Requests are
single-flight: a slow inference on the Pi's CPU never queues up a backlog —
the next tick uses whichever frame is newest then.
"""

from __future__ import annotations

import json
import logging
import time

import requests

from .persona import Scene

log = logging.getLogger("wavego-pet")

SYSTEM_PROMPT = (
    "You are the eyes of a robot pet. Look at the frame and answer with ONLY "
    'minified JSON, no markdown, no extra text: '
    '{"person":<bool>,"position":"left|center|right|none",'
    '"gesture":"wave|point|none"}. '
    "position = where the person stands in the frame; gesture = what their "
    "hands are doing. If no person: person=false and the strings are \"none\"."
)

DEFAULT_TEMPERATURE = 0.2   # structure matters, not creativity
DEFAULT_MAX_TOKENS = 60     # the answer is ~25 tokens; cap the damage


def extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of a model reply. Tolerates
    markdown fences and chatty prefixes; returns None when there is none."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def scene_from_reply(text: str) -> Scene:
    data = extract_json(text)
    if not isinstance(data, dict):
        return Scene.nothing()
    person = bool(data.get("person", False))
    position = data.get("position", "none")
    gesture = data.get("gesture", "none")
    if position not in ("left", "center", "right"):
        # a visible person with an unreadable position is treated as centered
        position = "center" if person else "none"
    if gesture not in ("wave", "point"):
        gesture = "none"
    if not person:
        return Scene.nothing()
    return Scene(person_visible=True, position=position, gesture=gesture)


class VLMClient:

    def __init__(self, url: str = "http://127.0.0.1:8080",
                 model: str = "qwen3-vl-2b", timeout_s: float = 120.0,
                 temperature: float = DEFAULT_TEMPERATURE,
                 max_tokens: int = DEFAULT_MAX_TOKENS):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._busy = False
        self.last_latency_s: float | None = None

    def describe(self, jpeg_bytes: bytes) -> Scene | None:
        """Ask about one JPEG frame. None = skipped (busy) or failed; either
        way the pet just keeps doing what it was doing."""
        if self._busy:
            return None
        self._busy = True
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Describe this frame."},
                        {"type": "image_url", "image_url": {
                            "url": "data:image/jpeg;base64,"
                                   + _b64(jpeg_bytes)}},
                    ]},
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            t0 = time.monotonic()
            resp = requests.post(f"{self.url}/v1/chat/completions",
                                 json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            self.last_latency_s = time.monotonic() - t0
            scene = scene_from_reply(text)
            log.debug("scene=%s latency=%.1fs", scene, self.last_latency_s)
            return scene
        except Exception as exc:
            log.warning("VLM request failed: %s", exc)
            return None
        finally:
            self._busy = False


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


class FakeVLM:
    """Scripted VLM for --mock mode and tests: pops one scene per call,
    repeating the last one forever after the script runs out."""

    def __init__(self, script: list[Scene]):
        self.script = list(script)
        self._last: Scene = Scene.nothing()
        self.calls = 0

    def describe(self, jpeg_bytes: bytes) -> Scene | None:
        if self.script:
            self._last = self.script.pop(0)
        self.calls += 1
        return self._last
