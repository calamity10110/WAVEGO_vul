"""Scene validator — a second opinion on what the VLM claims to see.

Rule: the validator and the VLM may never be the SAME model (same
provider+endpoint+model). One model checking its own hallucinations is not
a check — the whole point is an independent opinion (mirrors the two-layer
verifier in ugv_rpi/vlm_ctrl and the TinyVLM finding that small VLMs are
weak on position/count claims: verify before acting).

Backends:
- software  — no second model: a scene claim must repeat for N consecutive
              frames before the pet acts on it (temporal consistency).
- host      — a second LOCAL model (llama-server on another port/model).
- cloud     — a hosted OpenAI-compatible endpoint (api key via env var).

A validator that fails to answer downgrades the scene to "nothing seen":
the pet stays calm — the supervisor finishes whatever is already running.
"""

from __future__ import annotations

import logging
import os

import requests

from .persona import Scene
from .vlm_client import extract_json

log = logging.getLogger("wavego-pet")


class ConfigError(ValueError):
    """Invalid LLM/validator configuration (e.g. same model for both)."""


def validate_llm_config(vlm_cfg: dict, validator_cfg: dict) -> None:
    """Enforce: VLM and validator may not be the same provider+url+model."""
    def key(cfg: dict) -> tuple:
        return (cfg.get("provider", "host"),
                (cfg.get("url") or "").rstrip("/"),
                cfg.get("model") or "")

    if validator_cfg.get("provider", "software") in ("host", "cloud") \
            and key(vlm_cfg) == key(validator_cfg):
        raise ConfigError(
            "validator must not use the same model as the VLM "
            f"({key(validator_cfg)}) — use a different model/endpoint, or "
            "the software validator")


class SoftwareValidator:
    """Temporal consistency: hold the last CONFIRMED scene until a new
    claim repeats for `confirm_threshold` consecutive frames."""

    def __init__(self, confirm_threshold: int = 2):
        if confirm_threshold < 1:
            raise ValueError("confirm_threshold must be >= 1")
        self.confirm_threshold = confirm_threshold
        self._pending_key = None
        self._pending_count = 0
        self._confirmed = Scene.nothing()

    def confirm(self, scene: Scene, jpeg_bytes: bytes | None = None) -> Scene:
        key = (scene.person_visible, scene.position, scene.gesture)
        if key == self._pending_key:
            self._pending_count += 1
        else:
            self._pending_key = key
            self._pending_count = 1
        if self._pending_count >= self.confirm_threshold:
            self._confirmed = scene
        return self._confirmed


VERIFY_SYSTEM_PROMPT = (
    "You are a safety validator for a robot pet. You are shown a frame and "
    "a claim another model made about it. Verify strictly: is the claim "
    "clearly visible in THIS frame? Answer ONLY minified JSON, no markdown: "
    '{"verified":<bool>,"reason":"<short>"}'
)


class RemoteValidator:
    """Ask a second model (host llama-server or a cloud OpenAI-compatible
    endpoint) to verify the VLM's claim against the same frame."""

    def __init__(self, url: str, model: str,
                 api_key_env: str | None = None, timeout_s: float = 60.0):
        self.url = url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self._api_key = None
        if api_key_env:
            self._api_key = os.environ.get(api_key_env)
            if not self._api_key:
                raise ConfigError(
                    f"api key env var {api_key_env} is not set")

    def confirm(self, scene: Scene, jpeg_bytes: bytes | None = None) -> Scene:
        import base64

        claim = (f"claim: person={scene.person_visible}, "
                 f"position={scene.position}, gesture={scene.gesture}")
        content = [{"type": "text", "text": claim}]
        if jpeg_bytes:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,"
                       + base64.b64encode(jpeg_bytes).decode("ascii")}})
        headers = {"Authorization": f"Bearer {self._api_key}"} \
            if self._api_key else {}
        try:
            resp = requests.post(
                f"{self.url}/v1/chat/completions",
                headers=headers,
                json={"model": self.model,
                      "messages": [
                          {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
                          {"role": "user", "content": content},
                      ],
                      "temperature": 0.0,
                      "max_tokens": 40},
                timeout=self.timeout_s)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            log.warning("validator request failed: %s", exc)
            return Scene.nothing()
        verdict = extract_json(text) or {}
        if verdict.get("verified") is True:
            return scene
        log.info("validator rejected claim (%s): %s",
                 verdict.get("reason", "no reason"), claim)
        return Scene.nothing()


def build_validator(validator_cfg: dict) -> SoftwareValidator | RemoteValidator | None:
    provider = (validator_cfg or {}).get("provider", "software")
    if provider in (None, "none", "software"):
        return SoftwareValidator(
            confirm_threshold=int((validator_cfg or {}).get(
                "confirm_threshold", 2)))
    url = validator_cfg.get("url")
    model = validator_cfg.get("model")
    if not url or not model:
        raise ConfigError(
            f"validator provider '{provider}' needs url and model")
    return RemoteValidator(url=url, model=model,
                           api_key_env=validator_cfg.get("api_key_env"),
                           timeout_s=float(validator_cfg.get(
                               "timeout_s", 60.0)))
