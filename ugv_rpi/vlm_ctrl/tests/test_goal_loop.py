import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from vlm_ctrl import goal_loop as G


def make_loop(**kw):
    clock = {"t": 1000.0}
    loop = G.GoalLoop("find the ball", now=lambda: clock["t"], **kw)
    return loop, clock


def decision(label="forward", status="in_progress", conf="high", reason="moving"):
    return {"label": label, "goal_status": status, "reason": reason, "confidence": conf}


def test_start_and_dispatch():
    loop, _ = make_loop(verify_every_n=100)
    assert loop.start_cycle() == {"run": True}
    assert loop.next_attempt_engine() == "fast"
    d = loop.on_decision(decision("forward"))
    assert d == {"dispatch": "forward"}
    assert loop.cycles == 1


def test_reached_claim_requires_slow_confirmation():
    loop, _ = make_loop(verify_every_n=100)
    loop.start_cycle()
    d = loop.on_decision(decision("stop", status="reached"))
    assert d["dispatch"] is None and d["confirm_slow"] is True
    out = loop.on_slow_verdict(d["decision"], agrees=True)
    assert out["terminal"] == G.REACHED
    assert "send_stop" not in out


def test_reached_with_movement_label_sends_stop():
    loop, _ = make_loop(verify_every_n=100)
    loop.start_cycle()
    d = loop.on_decision(decision("forward", status="reached"))
    out = loop.on_slow_verdict(d["decision"], agrees=True)
    assert out["terminal"] == G.REACHED
    assert out["send_stop"] is True
    assert out["stop_cmd"] == {"T": 111, "FB": 0, "LR": 0}


def test_reached_claim_slow_disagreement_continues():
    loop, _ = make_loop(verify_every_n=100)
    loop.start_cycle()
    d = loop.on_decision(decision("stop", status="reached"))
    out = loop.on_slow_verdict(d["decision"], agrees=False, note="not visible")
    assert out.get("retry") is True and out["engine"] == "fast"
    assert loop.state == G.RUNNING


def test_low_confidence_triggers_slow_path():
    loop, _ = make_loop(verify_every_n=100)
    loop.start_cycle()
    d = loop.on_decision(decision("forward", conf="low"))
    assert d["confirm_slow"] is True and d["dispatch"] is None


def test_three_strikes_abort_with_stop():
    loop, _ = make_loop()
    loop.start_cycle()
    r1 = loop.on_attempt_failed(["bad"], "raw1")
    assert r1["retry"] and r1["engine"] == "fast"
    assert r1["feedback"] == ("raw1", ["bad"], loop.log[0])
    r2 = loop.on_attempt_failed(["still bad"], "raw2")
    assert r2["retry"] and r2["engine"] == "slow"
    r3 = loop.on_attempt_failed(["nope"], "raw3")
    assert r3["terminal"] == G.ABORTED_STRIKES
    assert r3["send_stop"] is True and r3["stop_cmd"] == {"T": 111, "FB": 0, "LR": 0}
    assert loop.next_attempt_engine() is None


def test_stall_triggers_replan_then_abort():
    loop, _ = make_loop(verify_every_n=100, stall_cycles=3)
    loop.start_cycle()
    for _ in range(3):
        out = loop.on_decision(decision("forward"))
    assert out.get("reason") == "stall_replan"
    assert out["confirm_slow"] is True and out["dispatch"] is None
    for _ in range(3):
        out = loop.on_decision(decision("forward"))
    assert out["terminal"] == G.ABORTED_STALL
    assert out["send_stop"] is True


def test_stall_reset_by_non_in_progress():
    loop, _ = make_loop(verify_every_n=100, stall_cycles=3)
    loop.start_cycle()
    for _ in range(3):
        loop.on_decision(decision("forward"))
    loop.on_decision(decision("stop", status="reached"))
    loop.on_slow_verdict({"label": "stop", "goal_status": "reached",
                          "reason": "done", "confidence": "high"}, agrees=True)
    assert loop.state == G.REACHED


def test_cycle_cap_timeout():
    loop, _ = make_loop(max_cycles=2, verify_every_n=100)
    loop.start_cycle()
    loop.on_decision(decision("none"))
    loop.on_decision(decision("none"))
    out = loop.start_cycle()
    assert out["run"] is False and out["terminal"] == G.TIMEOUT
    assert out["send_stop"] is True


def test_wall_clock_timeout():
    loop, clock = make_loop(max_seconds=10.0, verify_every_n=100)
    loop.start_cycle()
    clock["t"] += 11.0
    out = loop.start_cycle()
    assert out["terminal"] == G.TIMEOUT


def test_user_stop():
    loop, _ = make_loop()
    loop.start_cycle()
    out = loop.request_stop()
    assert out["terminal"] == G.STOPPED_USER and out["send_stop"] is True


def test_status_shape():
    loop, _ = make_loop()
    loop.start_cycle()
    loop.on_decision(decision("forward"))
    s = loop.status()
    assert s["state"] == G.RUNNING and s["cycles"] == 1
    assert s["goal"] == "find the ball"
    assert s["recent_labels"] == ["forward"]


def test_terminal_start_cycle_refused():
    loop, _ = make_loop()
    loop.request_stop()
    out = loop.start_cycle()
    assert out["run"] is False and out["reason"] == G.STOPPED_USER
