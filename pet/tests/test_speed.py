"""Inference-speed tests — change-gated inference and boot warmup.

The detector tests use raw numpy frames (no cv2 decode); the brain tests
use fake detectors so the pipeline behavior is covered without cv2.
"""

import numpy as np

from wavego_pet.vision import FrameChangeDetector


def frame_with(value, size=64):
    return np.full((size, size, 3), value, dtype=np.uint8)


def test_identical_frame_is_no_change():
    d = FrameChangeDetector(threshold=3.0)
    assert d.changed_frame(frame_with(100)) is True    # first frame: "changed"
    assert d.changed_frame(frame_with(100)) is False   # same scene
    assert d.changed_frame(frame_with(100)) is False


def test_scene_change_detects_movement():
    d = FrameChangeDetector(threshold=3.0)
    d.changed_frame(frame_with(100))
    assert d.changed_frame(frame_with(160)) is True    # +60 mean shift


def test_small_noise_below_threshold_is_ignored():
    d = FrameChangeDetector(threshold=3.0)
    base = frame_with(100)
    d.changed_frame(base)
    noisy = frame_with(101)                            # +1: sensor noise
    assert d.changed_frame(noisy) is False


def test_resolution_change_is_normalized():
    # thumbnails are fixed-size: capture resolution alone must not read
    # as scene change
    d = FrameChangeDetector()
    d.changed_frame(frame_with(100))
    assert d.changed_frame(frame_with(100, size=32)) is False
    assert d.changed_frame(frame_with(160, size=32)) is True


def test_threshold_zero_reports_any_change():
    d = FrameChangeDetector(threshold=0.0)
    d.changed_frame(frame_with(100))
    assert d.changed_frame(frame_with(101)) is True


def test_changed_jpeg_decodes_or_fails_true():
    from wavego_pet.vision import TINY_JPEG

    d = FrameChangeDetector()
    try:
        import cv2  # noqa: F401

        assert d.changed_jpeg(TINY_JPEG) is True       # first frame
        assert d.changed_jpeg(TINY_JPEG) is False      # same image
    except ImportError:
        assert d.changed_jpeg(TINY_JPEG) is True       # no cv2: assume change


# -------------------------------------------------- brain change gating

def test_brain_skips_vlm_when_scene_static():
    from wavego_pet.persona import Scene

    brain, supervisor, transport, clock, _ = _make_brain_with_detector(
        [Scene(person_visible=True)], detector_changes=[True, False, False])
    for _ in range(3):
        clock.t += 13          # past the rate limit every time
        brain.tick()
    assert brain.vlm.calls == 1                     # only the changed frame


def test_brain_calls_vlm_when_scene_changes():
    from wavego_pet.persona import Scene

    brain, supervisor, transport, clock, _ = _make_brain_with_detector(
        [Scene(person_visible=True), Scene(person_visible=True,
                                           position="left")],
        detector_changes=[True, True])
    for _ in range(2):
        clock.t += 13
        brain.tick()
    assert brain.vlm.calls == 2


def _make_brain_with_detector(script, detector_changes):
    from wavego_pet.brain import PetBrain
    from wavego_pet.persona import PersonaConfig, PetPersona
    from wavego_agent.supervisor import MotionSupervisor
    from wavego_agent.telemetry import MockTelemetry
    from wavego_agent.transport import MockTransport
    from wavego_pet.vision import StaticFrames, TINY_JPEG
    from wavego_pet.vlm_client import FakeVLM

    class Det:
        def __init__(self, seq):
            self.seq = list(seq)

        def changed_jpeg(self, jpeg):
            return self.seq.pop(0) if self.seq else True

    class CountingFakeVLM:
        def __init__(self, script):
            self._fake = FakeVLM(script)
            self.calls = 0

        def describe(self, jpeg):
            self.calls += 1
            return self._fake.describe(jpeg)

    clock = type("C", (), {"t": 1000.0, "__call__": lambda self: self.t})()
    transport = MockTransport()
    transport.open()
    supervisor = MotionSupervisor(transport, telemetry=MockTelemetry(),
                                  clock=clock)
    persona = PetPersona(PersonaConfig(), clock=clock)
    brain = PetBrain(supervisor, CountingFakeVLM(script),
                     StaticFrames([TINY_JPEG]), persona, clock=clock,
                     scene_every_s=12.0,
                     change_detector=Det(detector_changes))
    return brain, supervisor, transport, clock, []


def test_warmup_swallows_failure_and_reports():
    from wavego_pet.brain import PetBrain
    from wavego_pet.persona import PersonaConfig, PetPersona
    from wavego_agent.supervisor import MotionSupervisor
    from wavego_agent.telemetry import MockTelemetry
    from wavego_agent.transport import MockTransport
    from wavego_pet.vision import StaticFrames
    from wavego_pet.vlm_client import FakeVLM

    class Boom:
        def describe(self, jpeg):
            raise RuntimeError("llama-server not up yet")

    events = []
    clock = type("C", (), {"t": 1000.0, "__call__": lambda self: self.t})()
    transport = MockTransport()
    transport.open()
    supervisor = MotionSupervisor(transport, telemetry=MockTelemetry(),
                                  clock=clock)
    brain = PetBrain(supervisor, Boom(), StaticFrames([]),
                     PetPersona(PersonaConfig(), clock=clock), clock=clock,
                     on_event=events.append)
    brain.warmup()                      # must not raise
    assert events == []                 # failure: no warmup_done event

    from wavego_pet.vision import TINY_JPEG

    brain_ok = PetBrain(supervisor, FakeVLM([]), StaticFrames([TINY_JPEG]),
                        PetPersona(PersonaConfig(), clock=clock), clock=clock,
                        on_event=events.append)
    brain_ok.warmup()
    assert any(e["event"] == "warmup_done" for e in events)
