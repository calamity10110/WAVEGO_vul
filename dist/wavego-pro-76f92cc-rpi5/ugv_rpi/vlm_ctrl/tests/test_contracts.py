import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from vlm_ctrl import contracts as C


def test_derive_commands_exact():
    assert C.derive_command("forward") == {"T": 111, "FB": 1, "LR": 0}
    assert C.derive_command("back") == {"T": 111, "FB": -1, "LR": 0}
    assert C.derive_command("turn_left") == {"T": 111, "FB": 0, "LR": -1}
    assert C.derive_command("turn_right") == {"T": 111, "FB": 0, "LR": 1}
    assert C.derive_command("stop") == {"T": 111, "FB": 0, "LR": 0}
    assert C.derive_command("sit") == {"T": 112, "func": 1}
    assert C.derive_command("stand") == {"T": 110}
    assert C.derive_command("jump") == {"T": 112, "func": 3}
    assert C.derive_command("none") is None


def test_derive_unknown_raises():
    try:
        C.derive_command("dance")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_derive_returns_copy():
    d1 = C.derive_command("forward")
    d1["FB"] = 99
    assert C.derive_command("forward")["FB"] == 1


def test_validate_decision_valid_full():
    clean, errors = C.validate_decision({
        "label": "forward", "goal_status": "in_progress",
        "reason": "target ahead", "confidence": "high",
    })
    assert errors == []
    assert clean == {"label": "forward", "goal_status": "in_progress",
                     "reason": "target ahead", "confidence": "high"}


def test_validate_decision_minimal_defaults_confidence():
    clean, errors = C.validate_decision({
        "label": "none", "goal_status": "reached", "reason": "ball is in view",
    })
    assert errors == []
    assert clean["confidence"] == "medium"


def test_validate_decision_rejects_bad_label():
    clean, errors = C.validate_decision({
        "label": "fly", "goal_status": "in_progress", "reason": "wings out",
    })
    assert clean is None and errors


def test_validate_decision_rejects_bad_status():
    clean, errors = C.validate_decision({
        "label": "stop", "goal_status": "maybe", "reason": "not sure",
    })
    assert clean is None and errors


def test_validate_decision_rejects_reason_bounds():
    for reason in ("no", "x" * 161):
        clean, errors = C.validate_decision({
            "label": "stop", "goal_status": "in_progress", "reason": reason,
        })
        assert clean is None and errors, reason


def test_validate_decision_rejects_extra_keys():
    clean, errors = C.validate_decision({
        "label": "stop", "goal_status": "in_progress", "reason": "all fine",
        "T": 111,
    })
    assert clean is None and errors


def test_validate_decision_rejects_non_dict():
    clean, errors = C.validate_decision(["stop"])
    assert clean is None and errors


def test_private_keys_bypass_schema_and_reattach():
    clean, errors = C.validate_decision({
        "label": "forward", "goal_status": "in_progress", "reason": "going",
        "_id_conf": 0.83, "_frame_hash": "abc",
    })
    assert errors == []
    assert clean["_id_conf"] == 0.83 and clean["_frame_hash"] == "abc"


def test_private_keys_do_not_mask_schema_errors():
    clean, errors = C.validate_decision({
        "label": "warp", "goal_status": "in_progress", "reason": "going",
        "_id_conf": 0.83,
    })
    assert clean is None and errors


def test_whitelist_ts_subset():
    assert C.WHITELIST_TS == frozenset({110, 111, 112})
