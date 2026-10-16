"""PetBrain tests — the eyes-to-motion pipeline, end to end, mocked.

FakeVLM + StaticFrames + MockTransport: a scripted "person appears, waves,
leaves" scenario must land as real semantic commands in the supervisor, at
the configured cadence, with failures swallowed.
"""


from wavego_agent.supervisor import MotionSupervisor
from wavego_agent.telemetry import MockTelemetry
from wavego_agent.transport import MockTransport

from wavego_pet.brain import PetBrain
from wavego_pet.persona import PersonaConfig, PetPersona, Scene
from wavego_pet.vision import StaticFrames, TINY_JPEG
from wavego_pet.vlm_client import FakeVLM


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_brain(script, persona_cfg=None, scene_every_s=12.0):
    clock = Clock()
    transport = MockTransport()
    transport.open()
    supervisor = MotionSupervisor(transport, telemetry=MockTelemetry(),
                                  clock=clock)
    persona = PetPersona(persona_cfg or PersonaConfig(), clock=clock)
    brain = PetBrain(supervisor, FakeVLM(script), StaticFrames([TINY_JPEG]),
                     persona, clock=clock, scene_every_s=scene_every_s)
    events = []
    brain._on_event = events.append
    return brain, supervisor, transport, clock, events


def drain(brain, supervisor, clock, n=10, dt=1.0):
    """Tick the brain and the supervisor together so commands get started."""
    for _ in range(n):
        clock.t += dt
        brain.tick()
        supervisor.tick()


def test_scene_rate_limit_holds():
    brain, _, _, clock, _ = make_brain(
        [Scene(person_visible=True)], scene_every_s=12.0)
    assert brain.tick() is True
    clock.t += 1
    assert brain.tick() is False               # too soon
    clock.t += 12
    assert brain.tick() is True                # script exhausted -> no-op scene


def test_no_frame_means_no_vlm_call():
    brain, _, _, clock, _ = make_brain([Scene(person_visible=True)])
    brain.frames = StaticFrames([])            # never returns a frame
    assert brain.tick() is False


def test_person_appearance_flows_through_to_motion():
    brain, supervisor, transport, clock, events = make_brain(
        [Scene(person_visible=True, position="center", gesture="none")])
    drain(brain, supervisor, clock, n=8)
    # greet chain reached the wire: gait + gimbal look (+ jump from the
    # first-greet trick) then the walk's stop
    native = transport.sent
    assert {"T": 111, "FB": 1, "LR": 0} in native          # walk gait
    assert {"T": 133, "X": 0.0, "Y": -17.14} in native     # lean up
    assert {"T": 112, "func": 3} in native                 # jump
    assert any(e["event"] == "pet_command" for e in events)


def test_wave_trick_reaches_the_wire():
    brain, supervisor, transport, clock, _ = make_brain(
        [Scene(person_visible=True, position="center", gesture="wave")],
        persona_cfg=PersonaConfig(greet_trick=False, trick_cooldown_s=5))
    drain(brain, supervisor, clock, n=45, dt=0.5)
    # scene 1 (appearance): greet walk, no jump; scene 2 (12 s later, same
    # wave repeated): the trick — jump plus spin gait
    assert {"T": 112, "func": 3} in transport.sent
    assert any(c.get("FB") == 0 and c.get("LR") in (-1, 1)
               for c in transport.sent)                    # the spin gait


def test_vlm_failure_keeps_pet_calm():
    brain, supervisor, transport, clock, _ = make_brain(
        [Scene(person_visible=True, position="center", gesture="none")])
    brain.vlm = FakeVLM([])                   # always Scene.nothing()
    drain(brain, supervisor, clock, n=3)
    assert transport.sent == []


def test_estop_swallows_pet_commands():
    brain, supervisor, transport, clock, _ = make_brain(
        [Scene(person_visible=True, position="center", gesture="none")])
    supervisor.emergency_stop("test")
    drain(brain, supervisor, clock, n=5)      # must not raise
    assert supervisor.estop_active


# ---------------------------------------------------------- display wiring

class FakeDisplay:
    def __init__(self):
        self.updates = []

    def update(self, state, scene="", extra="", stats_text=""):
        self.updates.append((state, scene, extra, stats_text))


def test_brain_updates_display_after_each_scene():
    clock = Clock()
    transport = MockTransport()
    transport.open()
    supervisor = MotionSupervisor(transport, telemetry=MockTelemetry(),
                                  clock=clock)
    persona = PetPersona(PersonaConfig(), clock=clock)
    display = FakeDisplay()
    brain = PetBrain(supervisor, FakeVLM(
        [__import__("wavego_pet.persona", fromlist=["Scene"]).Scene(
            person_visible=True, position="left", gesture="none")]),
        StaticFrames([TINY_JPEG]), persona, clock=clock,
        scene_every_s=12.0, display=display)
    clock.t += 1
    brain.tick()
    assert display.updates, "display not refreshed"
    state, scene, _, _ = display.updates[0]
    assert state == persona.state.value
    assert "person" in scene


def test_brain_display_failures_are_swallowed():
    class Broken:
        def update(self, *a, **k):
            raise RuntimeError("DRM exploded")

    clock = Clock()
    transport = MockTransport()
    transport.open()
    supervisor = MotionSupervisor(transport, telemetry=MockTelemetry(),
                                  clock=clock)
    persona = PetPersona(PersonaConfig(), clock=clock)
    brain = PetBrain(supervisor, FakeVLM([]), StaticFrames([TINY_JPEG]),
                     persona, clock=clock, scene_every_s=12.0, display=Broken())
    clock.t += 1
    brain.tick()            # must not raise


# --------------------------------------------------- validator pipeline

def test_flaky_vlm_claims_stabilized_by_software_validator():
    # VLM alternates person/nothing: threshold 2 means nothing is ever
    # confirmed, so the pet must not greet
    from wavego_pet.persona import Scene as S
    from wavego_pet.validator import SoftwareValidator

    brain, supervisor, transport, clock, _ = make_brain(
        [S(person_visible=True), S(), S(person_visible=True), S()])
    brain.validator = SoftwareValidator(confirm_threshold=2)
    drain(brain, supervisor, clock, n=8)
    assert transport.sent == []


def test_stable_person_claim_passes_validator_and_greets():
    from wavego_pet.persona import Scene as S
    from wavego_pet.validator import SoftwareValidator

    brain, supervisor, transport, clock, _ = make_brain(
        [S(person_visible=True, position="center")])
    brain.validator = SoftwareValidator(confirm_threshold=2)
    # scene 1: not confirmed (first claim); scene 2 (12 s later): confirmed
    drain(brain, supervisor, clock, n=30, dt=1.0)
    assert {"T": 111, "FB": 1, "LR": 0} in transport.sent
