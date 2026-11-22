"""MiMo ASR/TTS integration tests for the voice assistant.

Covers: WAV framing for the API's data-URI input, ASR request shape
(model, Authorization, input_audio, asr_options), TTS request shape
(style + speak messages, audio voice/format) and playback dispatch,
provider routing (mimo primary, moonshine fallback), and the spoken
ack hook in execute(). Network is mocked; no key or audio device is
needed to run the suite.
"""

import base64
import importlib
import io
import os
import sys
import types
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

va = importlib.import_module("voice.voice_assistant")


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def mimo_key(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "test-key-123")


def make_cfg(**overrides):
    cfg = {
        "api_base": "http://127.0.0.1:5000",
        "mic": {"sample_rate": 16000},
        "speaker": {"feedback": False, "aplay_device": "plughw:0,0"},
        "asr": {
            "provider": "mimo",
            "fallback": "moonshine",
            "model": "tiny",
            "mimo": {"base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
                     "model": "mimo-v2.5-asr", "language": "zh"},
        },
        "tts": {"enabled": True,
                "mimo": {"base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
                         "model": "mimo-v2.5-tts", "voice": "Chloe", "format": "wav"},
                "say": {"ok": "Okay", "unknown": "Sorry", "stopped": "Stopped"}},
        "vad": {},
        "commands": [],
    }
    cfg.update(overrides)
    return cfg


def test_pcm16_to_wav_roundtrip():
    audio = (np.sin(np.linspace(0, 100, 16000)) * 20000).astype(np.int16)
    raw = va.pcm16_to_wav(audio, 16000)
    with wave.open(io.BytesIO(raw)) as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000
        assert np.frombuffer(wf.readframes(16000), dtype=np.int16).tolist() == audio.tolist()


def test_mimo_asr_request_shape(mimo_key, monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResp({"choices": [{"message": {"content": " walk forward "}}]})

    monkeypatch.setattr(va.requests, "post", fake_post)
    asr = va.MiMoAsr({"base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
                      "model": "mimo-v2.5-asr", "language": "zh", "timeout_s": 9})
    audio = np.zeros(1600, dtype=np.int16)
    text = asr.transcribe(audio, 16000)

    assert text == "walk forward"
    assert seen["url"] == "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key-123"
    assert seen["timeout"] == 9
    assert seen["json"]["model"] == "mimo-v2.5-asr"
    assert seen["json"]["asr_options"] == {"language": "zh"}
    content = seen["json"]["messages"][0]["content"]
    assert content[0]["type"] == "input_audio"
    assert content[0]["input_audio"]["data"].startswith("data:audio/wav;base64,")
    wav_bytes = base64.b64decode(content[0]["input_audio"]["data"].split(",", 1)[1])
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        assert wf.getnframes() == 1600


def test_mimo_asr_missing_key_raises(monkeypatch):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    asr = va.MiMoAsr({})
    assert not asr.available
    with pytest.raises(RuntimeError):
        asr.transcribe(np.zeros(100, dtype=np.int16), 16000)


def test_mimo_asr_list_content_joined(mimo_key, monkeypatch):
    monkeypatch.setattr(va.requests, "post", lambda *a, **k: FakeResp(
        {"choices": [{"message": {"content": [{"type": "text", "text": "hi "},
                                               {"type": "text", "text": "there"}]}}]}))
    asr = va.MiMoAsr({})
    assert asr.transcribe(np.zeros(10, dtype=np.int16), 16000) == "hi there"


def test_tts_speak_payload_and_playback(mimo_key, monkeypatch, tmp_path):
    seen = {}
    wav_pcm = va.pcm16_to_wav(np.zeros(800, dtype=np.int16), 16000)

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return FakeResp({"choices": [{"message": {"audio": {
            "data": base64.b64encode(wav_pcm).decode()}}}]})

    played = {}
    monkeypatch.setattr(va.requests, "post", fake_post)
    monkeypatch.setattr(va.subprocess, "run",
                        lambda cmd, **kw: played.update(cmd=cmd, kw=kw))
    monkeypatch.setattr(va, "HERE", str(tmp_path))

    tts = va.MiMoTts({"enabled": True,
                      "mimo": {"base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
                               "voice": "Chloe", "format": "wav"},
                      "say": {"ok": "Okay"}}, "plughw:1,0")
    assert tts.phrase("ok") == "Okay"
    assert tts._speak("Okay") is True

    assert seen["url"] == "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key-123"
    msgs = seen["json"]["messages"]
    assert msgs[1] == {"role": "assistant", "content": "Okay"}
    assert msgs[0]["role"] == "user"
    assert seen["json"]["audio"] == {"format": "wav", "voice": "Chloe"}
    assert played["cmd"][:3] == ["aplay", "-q", "-D"]
    assert played["cmd"][4].endswith("tts_last.wav")
    with open(played["cmd"][4], "rb") as fh:
        assert fh.read() == wav_pcm


def test_tts_say_gating(monkeypatch):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    assert va.MiMoTts({"enabled": False}, "plughw:0,0").say("hi") is False
    assert va.MiMoTts({"enabled": True}, "plughw:0,0").say("hi") is False
    monkeypatch.setenv("MIMO_API_KEY", "k")
    assert va.MiMoTts({"enabled": True}, "plughw:0,0").say("") is False


def test_assistant_mimo_primary_transcribe(mimo_key, monkeypatch):
    assistant = va.VoiceAssistant(make_cfg())
    assistant.load_model()
    assert assistant.allow_local is True
    assert assistant.mimo_asr.available

    monkeypatch.setattr(va.requests, "post",
                        lambda *a, **k: FakeResp({"choices": [{"message": {
                            "content": "Walk Forward!"}}]}))
    assert assistant.transcribe(np.zeros(100, dtype=np.int16)) == "walk forward"
    assert assistant.model is None


def test_assistant_fallback_to_moonshine(mimo_key, monkeypatch):
    assistant = va.VoiceAssistant(make_cfg())
    assistant.load_model()

    def boom(*a, **k):
        raise ConnectionError("no network")
    monkeypatch.setattr(va.requests, "post", boom)

    stub = types.ModuleType("useful_moonshine_onnx")

    class FakeMoonshine:
        def __init__(self, model_name):
            self.model_name = model_name

        def transcribe(self, audios):
            return ["stand up"]
    stub.MoonshineModel = FakeMoonshine
    monkeypatch.setitem(sys.modules, "useful_moonshine_onnx", stub)

    assert assistant.transcribe(np.zeros(100, dtype=np.int16)) == "stand up"
    assert assistant.model is not None


def test_assistant_no_key_falls_back(mimo_key, monkeypatch):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    stub = types.ModuleType("useful_moonshine_onnx")

    class FakeMoonshine:
        def __init__(self, model_name):
            pass

        def transcribe(self, audios):
            return ["jump"]
    stub.MoonshineModel = FakeMoonshine
    monkeypatch.setitem(sys.modules, "useful_moonshine_onnx", stub)

    assistant = va.VoiceAssistant(make_cfg())
    assistant.load_model()
    assert assistant.mimo_asr is not None and not assistant.mimo_asr.available
    assert assistant.allow_local is True
    assert assistant.transcribe(np.zeros(100, dtype=np.int16)) == "jump"


def test_execute_speaks_ack(mimo_key):
    assistant = va.VoiceAssistant(make_cfg())
    spoken = []
    assistant.tts.say = lambda text: spoken.append(text) or True
    assistant.api.send_cmd = lambda cmd: True
    assistant.execute({"phrases": ["stand"], "cmd": {"T": 110}})
    assert spoken == ["Okay"]


def test_execute_entry_say_override(mimo_key):
    assistant = va.VoiceAssistant(make_cfg())
    spoken = []
    assistant.tts.say = lambda text: spoken.append(text) or True
    assistant.api.send_cmd = lambda cmd: True
    assistant.execute({"phrases": ["stand"], "say": "Standing up",
                       "cmd": {"T": 110}})
    assert spoken == ["Standing up"]


def test_movement_still_arms_timeout(mimo_key):
    assistant = va.VoiceAssistant(make_cfg())
    assistant.tts.say = lambda text: True
    assistant.api.send_cmd = lambda cmd: True
    assistant.execute({"phrases": ["forward"], "cmd": {"T": 111, "FB": 1, "LR": 0}})
    assert assistant.stop_deadline is not None
    assistant.execute({"phrases": ["stop"], "cmd": {"T": 111, "FB": 0, "LR": 0}})
    assert assistant.stop_deadline is None
