"""Validator tests — the second-opinion layer.

Covers: the same-model rule (VLM and validator may never share
provider+url+model), the software validator's temporal consistency, and the
remote validator's verify-refute-degrade contract.
"""

import pytest

from wavego_pet.persona import Scene
from wavego_pet.validator import (
    ConfigError,
    RemoteValidator,
    SoftwareValidator,
    build_validator,
    validate_llm_config,
)


def seen(position="center", gesture="none"):
    return Scene(person_visible=True, position=position, gesture=gesture)


# --------------------------------------------------------- same-model rule

def test_same_model_rejected():
    vlm = {"provider": "host", "url": "http://127.0.0.1:8080",
           "model": "qwen3-vl-2b"}
    val = {"provider": "host", "url": "http://127.0.0.1:8080",
           "model": "qwen3-vl-2b"}
    with pytest.raises(ConfigError):
        validate_llm_config(vlm, val)


def test_software_validator_ignores_same_model_rule():
    vlm = {"provider": "host", "url": "http://127.0.0.1:8080",
           "model": "qwen3-vl-2b"}
    validate_llm_config(vlm, {"provider": "software"})   # fine


def test_different_model_or_endpoint_ok():
    vlm = {"provider": "host", "url": "http://127.0.0.1:8080",
           "model": "qwen3-vl-2b"}
    validate_llm_config(vlm, {"provider": "host",
                              "url": "http://127.0.0.1:8081",
                              "model": "qwen3-vl-2b"})
    validate_llm_config(vlm, {"provider": "host",
                              "url": "http://127.0.0.1:8080",
                              "model": "smolvlm2"})
    validate_llm_config(vlm, {"provider": "cloud",
                              "url": "https://api.openai.com/v1",
                              "model": "gpt-4o-mini"})


# ------------------------------------------------- software validator

def test_software_requires_repeat_before_confirming():
    v = SoftwareValidator(confirm_threshold=2)
    first = v.confirm(seen())
    assert first.person_visible is False          # single claim: not confirmed
    second = v.confirm(seen())
    assert second.person_visible is True          # repeated: confirmed


def test_software_holds_last_confirmed_on_flap():
    v = SoftwareValidator(confirm_threshold=2)
    v.confirm(seen())
    v.confirm(seen())                             # person confirmed
    v.confirm(Scene.nothing())                    # flaps once -> hold person
    assert v.confirm(Scene.nothing()).person_visible is False  # now confirmed


def test_software_threshold_one_confirms_immediately():
    v = SoftwareValidator(confirm_threshold=1)
    assert v.confirm(seen()).person_visible is True


def test_software_rejects_bad_threshold():
    with pytest.raises(ValueError):
        SoftwareValidator(confirm_threshold=0)


# -------------------------------------------------- remote validator

class FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def patch_post(monkeypatch, capture, content):
    def fake_post(url, json=None, headers=None, timeout=None):
        capture.update(url=url, payload=json, headers=headers)
        return FakeResponse(content)

    import wavego_pet.validator as vd
    monkeypatch.setattr(vd.requests, "post", fake_post)


def test_remote_verifies_claim(monkeypatch):
    capture = {}
    patch_post(monkeypatch, capture, '{"verified": true, "reason": "clear"}')
    v = RemoteValidator(url="http://127.0.0.1:8081", model="smolvlm2")
    out = v.confirm(seen(gesture="wave"), b"jpeg")
    assert out == seen(gesture="wave")
    assert "person=True" in capture["payload"]["messages"][1]["content"][0]["text"]
    assert capture["headers"] == {}


def test_remote_refutation_downgrades_to_nothing(monkeypatch):
    patch_post(monkeypatch, {}, '{"verified": false, "reason": "empty room"}')
    v = RemoteValidator(url="http://x", model="m")
    assert v.confirm(seen(), b"jpeg") == Scene.nothing()


def test_remote_garbage_reply_downgrades_to_nothing(monkeypatch):
    patch_post(monkeypatch, {}, "the model rambled, no json")
    v = RemoteValidator(url="http://x", model="m")
    assert v.confirm(seen(), b"jpeg") == Scene.nothing()


def test_remote_failure_downgrades_to_nothing(monkeypatch):
    def boom(url, json=None, headers=None, timeout=None):
        raise ConnectionError("down")

    import wavego_pet.validator as vd
    monkeypatch.setattr(vd.requests, "post", boom)
    v = RemoteValidator(url="http://x", model="m")
    assert v.confirm(seen(), b"jpeg") == Scene.nothing()


def test_remote_cloud_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TEST_VAL_KEY", "sk-val")
    capture = {}
    patch_post(monkeypatch, capture, '{"verified": true}')
    v = RemoteValidator(url="https://api.openai.com/v1", model="gpt-4o-mini",
                        api_key_env="TEST_VAL_KEY")
    v.confirm(seen(), b"jpeg")
    assert capture["headers"]["Authorization"] == "Bearer sk-val"


def test_remote_missing_api_key_fails_at_build():
    with pytest.raises(ConfigError):
        RemoteValidator(url="https://x", model="m",
                        api_key_env="DEFINITELY_NOT_SET_ENV_VAR")


# --------------------------------------------------------- factory

def test_build_validator_software_default():
    v = build_validator({})
    assert isinstance(v, SoftwareValidator)
    v = build_validator({"provider": "software", "confirm_threshold": 3})
    assert v.confirm_threshold == 3


def test_build_validator_remote_requires_url_and_model():
    with pytest.raises(ConfigError):
        build_validator({"provider": "cloud", "model": "gpt-4o-mini"})
    v = build_validator({"provider": "host",
                         "url": "http://127.0.0.1:8081",
                         "model": "smolvlm2"})
    assert isinstance(v, RemoteValidator)
