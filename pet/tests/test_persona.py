"""Persona tests — the pet's personality contract.

Every rule that makes it pet-like AND safe: greeting only on appearance,
cooldowns hold, tricks are rare, follow bursts are short and slow, sleep
comes after a long quiet stretch, and NOTHING exceeds the safety clamps.
"""

import pytest

from wavego_pet.persona import (
    MAX_TURN_DURATION_S,
    MAX_WALK_DURATION_S,
    MAX_WALK_SPEED,
    PersonaConfig,
    PetPersona,
    PetState,
    Scene,
)


def seen(position="center", gesture="none"):
    return Scene(person_visible=True, position=position, gesture=gesture)


def make(cfg=None):
    clock = {"t": 1000.0}
    persona = PetPersona(cfg or PersonaConfig(), clock=lambda: clock["t"])
    return persona, clock


def cmds_only(commands, action):
    return [c for c in commands if c["action"] == action]


# ----------------------------------------------------------------- sleep

def test_sleeps_after_long_quiet():
    p, clock = make(PersonaConfig(sleep_after_s=300))
    p.update(Scene(), now=clock["t"])
    clock["t"] += 301
    out = p.update(Scene(), now=clock["t"])
    assert p.state is PetState.SLEEPING
    assert out == [{"action": "sit"}]


def test_wakes_when_person_appears():
    p, clock = make(PersonaConfig(sleep_after_s=60))
    p.update(Scene(), now=clock["t"])
    clock["t"] += 61
    p.update(Scene(), now=clock["t"])            # asleep
    clock["t"] += 5
    out = p.update(seen(), now=clock["t"])
    assert p.state is PetState.IDLE
    assert out[0] == {"action": "stand"}
    assert out[1]["action"] == "look" and out[1]["tilt"] > 0


def test_sleeping_pet_ignores_empty_scenes():
    p, clock = make(PersonaConfig(sleep_after_s=10))
    p.update(Scene(), now=clock["t"])
    clock["t"] += 20
    p.update(Scene(), now=clock["t"])
    clock["t"] += 10
    assert p.update(Scene(), now=clock["t"]) == []


# ---------------------------------------------------------------- greeting

def test_greets_when_person_appears():
    p, clock = make()
    out = p.update(seen(), now=clock["t"])
    assert p.state is PetState.GREETING
    walks = cmds_only(out, "walk")
    assert walks and walks[0]["direction"] == "forward"
    assert any(c["action"] == "look" for c in out)
    assert any(c["action"] == "jump" for c in out)   # first greet gets a jump


def test_greeting_has_cooldown():
    # follow disabled: we want to isolate the greet cooldown, not watch the
    # pet legally start following the returned person instead
    p, clock = make(PersonaConfig(greet_cooldown_s=60, follow_cooldown_s=1e6))
    p.update(seen(), now=clock["t"])
    clock["t"] += 5
    p.update(Scene(), now=clock["t"])                # person left
    clock["t"] += 5
    out = p.update(seen(), now=clock["t"])           # reappears: cooldown holds
    assert out == []
    assert p.state is PetState.IDLE


def test_greet_after_cooldown_works_again():
    p, clock = make(PersonaConfig(greet_cooldown_s=60, trick_cooldown_s=30))
    p.update(seen(), now=clock["t"])
    clock["t"] += 61
    p.update(Scene(), now=clock["t"])
    clock["t"] += 1
    out = p.update(seen(), now=clock["t"])
    assert cmds_only(out, "walk")


# ------------------------------------------------------------------ tricks

def test_wave_triggers_trick():
    p, clock = make(PersonaConfig(greet_cooldown_s=60, greet_trick=False))
    p.update(seen(), now=clock["t"])                 # appearance: greet fires
    clock["t"] += 6
    p.update(seen(), now=clock["t"])                 # settle (follow burst)
    clock["t"] += 6
    out = p.update(seen(gesture="wave"), now=clock["t"])
    assert p.state is PetState.PLAYING
    assert any(c["action"] == "jump" for c in out)
    turns = cmds_only(out, "turn")
    assert turns and turns[0]["duration"] <= MAX_TURN_DURATION_S


def test_trick_cooldown_blocks_spam():
    p, clock = make(PersonaConfig(trick_cooldown_s=30, follow_cooldown_s=1e6))
    p.update(seen(), now=clock["t"])                 # greet consumes the trick window
    clock["t"] += 6
    p.update(seen(gesture="wave"), now=clock["t"])   # wave inside cooldown
    clock["t"] += 6
    out = p.update(seen(gesture="wave"), now=clock["t"])
    assert out == []


# ----------------------------------------------------------------- follow

def test_follows_centered_person():
    p, clock = make(PersonaConfig(follow_cooldown_s=5))
    p.update(seen(), now=clock["t"])                 # greet consumed appearance
    clock["t"] += 5
    out = p.update(seen(), now=clock["t"])
    walks = cmds_only(out, "walk")
    assert walks
    assert walks[0]["speed"] <= MAX_WALK_SPEED
    assert walks[0]["duration"] <= MAX_WALK_DURATION_S


def test_turns_toward_offcenter_person():
    p, clock = make()
    p.update(seen(), now=clock["t"])
    clock["t"] += 6
    out = p.update(seen(position="left"), now=clock["t"])
    assert cmds_only(out, "turn")[0]["direction"] == "left"
    clock["t"] += 6
    out = p.update(seen(position="right"), now=clock["t"])
    assert cmds_only(out, "turn")[0]["direction"] == "right"


# ------------------------------------------------------------------ fidget

def test_fidgets_when_bored_but_not_sleepy():
    p, clock = make(PersonaConfig(fidget_after_s=45, sleep_after_s=300))
    p.update(seen(), now=clock["t"])
    clock["t"] += 50
    out = p.update(Scene(), now=clock["t"])
    looks = cmds_only(out, "look")
    assert looks and looks[0]["pan"] != 0
    assert p.state is PetState.IDLE                  # not asleep yet


# ----------------------------------------------------------------- clamps

@pytest.mark.parametrize("position", ["left", "right", "center"])
def test_nothing_ever_exceeds_pet_safe_bounds(position):
    p, clock = make()
    scene = seen(position=position, gesture="wave")
    for _ in range(40):
        for raw in p.update(scene, now=clock["t"]):
            assert raw["action"] in ("walk", "turn", "look", "jump", "stand",
                                     "sit", "stop")
            if raw["action"] == "walk":
                assert raw["speed"] <= MAX_WALK_SPEED
                assert raw["duration"] <= MAX_WALK_DURATION_S
            if raw["action"] == "turn":
                assert raw["duration"] <= MAX_TURN_DURATION_S
        clock["t"] += 7


def test_all_emitted_commands_pass_the_validator():
    from wavego_agent.schema import parse_command

    p, clock = make()
    scene = seen(gesture="wave")
    for _ in range(30):
        for raw in p.update(scene, now=clock["t"]):
            parse_command(raw)          # raises on anything invalid
        clock["t"] += 7
