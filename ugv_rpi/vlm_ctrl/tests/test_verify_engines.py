import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from vlm_ctrl.verify import SoftwareVerifier
from vlm_ctrl.engines import OnlineVerifier, FastEngine, conf_to_enum, build_fast_questions


def md5(data):
    return hashlib.md5(data).hexdigest()


def dec(status="in_progress", conf="high", id_conf=0.9, label="forward"):
    return {"label": label, "goal_status": status, "reason": "r",
            "confidence": conf, "_id_conf": id_conf}


def test_claim_persists_across_two_frames():
    v = SoftwareVerifier()
    h1, h2 = md5(b"f1"), md5(b"f2")
    assert v.verify(dec("reached"), h1)[0] is None          # first sighting: pending
    assert v.verify(dec("reached"), h2)[0] is True          # second frame: believed


def test_claim_low_confidence_rejected():
    v = SoftwareVerifier()
    h1, h2 = md5(b"f1"), md5(b"f2")
    assert v.verify(dec("reached", id_conf=0.5), h1)[0] is False


def test_claim_different_status_resets():
    v = SoftwareVerifier()
    h1, h2, h3 = md5(b"f1"), md5(b"f2"), md5(b"f3")
    v.verify(dec("reached"), h1)
    assert v.verify(dec("impossible"), h2)[0] is None
    assert v.verify(dec("impossible"), h3)[0] is True


def test_same_frame_twice_stays_pending():
    v = SoftwareVerifier()
    h1 = md5(b"sameframe")
    assert v.verify(dec("reached"), h1)[0] is None
    assert v.verify(dec("reached"), h1)[0] is None


def test_low_conf_action_rejected():
    v = SoftwareVerifier()
    ok, _, note = v.verify(dec(conf="low", id_conf=0.4), md5(b"f"))
    assert ok is False and "too low" in note


def test_normal_action_accepted():
    v = SoftwareVerifier()
    ok, _, _ = v.verify(dec(), md5(b"f"))
    assert ok is True


def test_online_disabled_returns_none():
    ov = OnlineVerifier(base_url="", model="m")
    assert ov.enabled is False
    assert ov.verify("goal", dec())[0] is None


def test_online_agree_and_disagree():
    calls = []

    def poster_ok(payload):
        calls.append(payload)
        return FakeResp('{"verdict": "agree", "note": "ball visible"}')

    def poster_bad(payload):
        return FakeResp('{"verdict": "disagree", "note": "no ball"}')

    ov1 = OnlineVerifier(base_url="http://x", model="m", poster=poster_ok)
    ok, method, note = ov1.verify("find ball", dec())
    assert ok is True and method == "online" and note == "ball visible"
    assert "find ball" in calls[0]["messages"][0]["content"][0]["text"]

    ov2 = OnlineVerifier(base_url="http://x", model="m", poster=poster_bad)
    ok, _, _ = ov2.verify("find ball", dec())
    assert ok is False


def test_online_unreachable_degrades():
    def poster_boom(payload):
        raise ConnectionError("down")

    ov = OnlineVerifier(base_url="http://x", model="m", poster=poster_boom)
    ok, _, note = ov.verify("g", dec())
    assert ok is None and note == "unreachable"


def test_online_garbage_response_degrades():
    ov = OnlineVerifier(base_url="http://x", model="m",
                        poster=lambda p: FakeResp("I cannot answer that, sorry!"))
    assert ov.verify("g", dec())[0] is None


def test_conf_to_enum_bands():
    assert conf_to_enum(0.9) == "high"
    assert conf_to_enum(0.75) == "high"
    assert conf_to_enum(0.6) == "medium"
    assert conf_to_enum(0.3) == "low"


def test_fast_engine_decide_maps_fields():
    def fake_predict(request):
        return {"answers": {
            "action": {"decision": "turn_left", "confidence": 0.82},
            "goal_status": {"decision": "in_progress", "confidence": 0.91},
        }}

    eng = FastEngine("ckpt", "/tmp", predict_fn=fake_predict)
    d = eng.decide("find the ball", "/tmp/x.jpg")
    assert d["label"] == "turn_left"
    assert d["goal_status"] == "in_progress"
    assert d["confidence"] == "high"
    assert abs(d["_id_conf"] - 0.82) < 1e-9
    assert "0.82" in d["reason"]


def test_fast_engine_feedback_in_state():
    seen = {}

    def fake_predict(request):
        seen["state"] = request["state"]
        return {"answers": {"action": {"decision": "stop", "confidence": 0.9},
                            "goal_status": {"decision": "in_progress", "confidence": 0.9}}}

    eng = FastEngine("ckpt", "/tmp", predict_fn=fake_predict)
    eng.decide("g", "/tmp/x.jpg", feedback="prior attempt rejected: bad label")
    assert "rejected" in seen["state"]


class FakeResp:
    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_fast_questions_shape():
    q = build_fast_questions('follow the person')
    assert set(q['questions'].keys()) == {'action', 'goal_status'}
    assert len(q['questions']['action']['criteria']) == 9
    assert len(q['questions']['goal_status']['criteria']) == 3
    assert 'follow the person' in q['state']


def test_fast_questions_criteria_are_labels_and_statuses():
    q = build_fast_questions('g')
    assert 'turn_left' in q['questions']['action']['criteria']
    assert 'none' in q['questions']['action']['criteria']
    assert 'impossible' in q['questions']['goal_status']['criteria']
