"""Model client — the sole llama-server HTTP interface.

Purpose:
    Wraps llama-server (Qwen3-VL-2B) behind three semantic calls:
    describe (vision), parse (intent), decide (action). All use the
    same endpoint with different prompts. stdlib urllib only — no
    external HTTP library.

Dependencies:
    stdlib urllib.request, json.
    Optional: base64 for image encoding.

Interface:
    VLModel(url, timeout_s) — constructor
    .describe(image_b64, prompt) -> str     — what the VLM sees
    .parse_intent(text) -> dict             — {intent, slots, priority}
    .decide(context, tools) -> str          — tool-call response

Expected outcome:
    Returns raw model text; callers handle parsing + safety gates.
    Network failures raise ModelError; callers degrade gracefully.
"""

import base64
import json
import urllib.request
import urllib.error


class ModelError(Exception):
    pass


class VLModel:
    def __init__(self, url="http://127.0.0.1:8080", timeout_s=120):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def _chat(self, messages, tools=None, images=None):
        payload = {"messages": messages, "temperature": 0.1}
        if tools:
            payload["tools"] = tools
        if images:
            payload["image_data"] = [
                {"data": img} if isinstance(img, str) else img for img in images]

        req = urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.URLError as e:
            raise ModelError(f"llama-server unreachable: {e}") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ModelError(f"unexpected response shape: {e}") from e

    def describe(self, image_b64, prompt="Describe what you see in one sentence."):
        messages = [{"role": "user", "content": prompt}]
        return self._chat(messages, images=[image_b64])

    def parse_intent(self, text):
        messages = [
            {"role": "system", "content":
                "Parse the user command into JSON with keys: intent (string), "
                "slots (object), priority (int 1-5, 5=urgent). Respond with JSON only."},
            {"role": "user", "content": text}]
        raw = self._chat(messages)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"intent": "unknown", "slots": {}, "priority": 1, "raw": raw}

    def decide(self, context_text, tools):
        messages = [
            {"role": "system", "content":
                "You are the brain of a quadruped robot. Use the set_state tool "
                "to propose motion. Hex codes are 6 uppercase hex chars."},
            {"role": "user", "content": context_text}]
        return self._chat(messages, tools=tools)
