"""VLM client tests — reply parsing robustness and request shape.

The real llama-server is never contacted: requests.post is monkeypatched.
What matters: malformed model output degrades to "nothing seen", never to a
crash or a hallucinated command.
"""

import pytest

from wavego_pet.persona import Scene
from wavego_pet.vlm_client import FakeVLM, VLMClient, extract_json, scene_from_reply


# ------------------------------------------------------------ json extraction

@pytest.mark.parametrize("text,expected", [
    ('{"person":true,"position":"center","gesture":"none"}',
     {"person": True}),
    ('```json\n{"person":true}\n```', {"person": True}),
    ('Sure! Here is what I see: {"person":false,"position":"none"}',
     {"person": False}),
    ('{"person":true,"position":"left","gesture":"wave"} trailing chatter',
     {"person": True}),
    ('no json at all', None),
    ('{"unterminated', None),
])
def test_extract_json(text, expected):
    out = extract_json(text)
    if expected is None:
        assert out is None
    else:
        assert out["person"] is expected["person"]


@pytest.mark.parametrize("text,expected", [
    ('{"person":true,"position":"left","gesture":"wave"}',
     Scene(True, "left", "wave")),
    ('{"person":true,"position":"diagonal","gesture":"none"}',
     Scene(True, "center", "none")),          # bad enum -> neutral center
    ('{"person":false,"position":"left","gesture":"wave"}', Scene()),
    ('{"person":"yes"}', Scene(True, "center", "none")),   # truthy -> person
    ('garbage', Scene()),
])
def test_scene_from_reply(text, expected):
    scene = scene_from_reply(text)
    assert scene.person_visible is expected.person_visible
    if expected.person_visible:
        assert scene.position == expected.position
        assert scene.gesture == expected.gesture


def test_person_false_normalizes_everything():
    assert scene_from_reply('{"person":false,"position":"left"}') == Scene()


# --------------------------------------------------------------- request shape

class FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def patch_post(monkeypatch, capture,
               content='{"person":true,"position":"center","gesture":"none"}'):
    def fake_post(url, json=None, headers=None, timeout=None):
        capture["url"] = url
        capture["payload"] = json
        capture["headers"] = headers
        capture["timeout"] = timeout
        return FakeResponse(content)

    import wavego_pet.vlm_client as vc
    monkeypatch.setattr(vc.requests, "post", fake_post)


def test_describe_builds_openai_vision_payload(monkeypatch):
    capture = {}
    patch_post(monkeypatch, capture)
    client = VLMClient(url="http://127.0.0.1:8080/")
    scene = client.describe(b"\xff\xd8fakejpeg")
    assert scene.person_visible is True
    assert capture["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    messages = capture["payload"]["messages"]
    assert messages[0]["role"] == "system"
    image_parts = [p for p in messages[1]["content"] if p["type"] == "image_url"]
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert capture["payload"]["max_tokens"] <= 80
    assert capture["headers"] == {}          # host endpoint: no auth header


def test_describe_cloud_sends_bearer_token(monkeypatch):
    monkeypatch.setenv("TEST_VLM_KEY", "sk-secret")
    capture = {}
    patch_post(monkeypatch, capture)
    client = VLMClient(url="https://api.example.com/v1", model="gpt-4o-mini",
                       api_key_env="TEST_VLM_KEY")
    client.describe(b"x")
    assert capture["headers"]["Authorization"] == "Bearer sk-secret"


def test_missing_api_key_env_fails_loudly_at_build():
    import pytest

    from wavego_pet.vlm_client import VLMClient as C

    with pytest.raises(ValueError):
        C(api_key_env="DEFINITELY_NOT_SET_ENV_VAR")


def test_describe_failure_returns_none_not_crash(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise ConnectionError("llama-server down")

    import wavego_pet.vlm_client as vc
    monkeypatch.setattr(vc.requests, "post", boom)
    assert VLMClient().describe(b"x") is None


def test_describe_single_flight(monkeypatch):
    client = VLMClient()
    client._busy = True                       # simulate an in-flight request
    assert client.describe(b"x") is None
    assert client._busy is True               # untouched by the skip path
    client._busy = False
    patch_post(monkeypatch, {})
    client.describe(b"x")
    assert client._busy is False              # released after success


# ------------------------------------------------------------------ fake vlm

def test_fake_vlm_scripts_then_repeats_last():
    fake = FakeVLM([Scene(person_visible=True), Scene()])
    assert fake.describe(b"") == Scene(person_visible=True)
    assert fake.describe(b"") == Scene()
    assert fake.describe(b"") == Scene()      # script exhausted: last repeats
