"""PetBrain — the loop that ties eyes to motion.

Frames -> VLM -> persona -> supervisor. Rate-limited by ``scene_every_s``
(the Pi's VLM call takes ~10-20 s; ticks in between are no-ops). Runs in its
own thread conceptually; the motion supervisor's tick thread is separate, so
a slow VLM call can never block or starve motion — worst case the pet reacts
late, which is exactly how pets behave anyway.
"""

from __future__ import annotations

import logging
import time

from wavego_agent.schema import CommandRejected, parse_command
from wavego_agent.supervisor import EStopActive, MotionSupervisor, QueueFullError

from .persona import PetPersona, Scene
from .vlm_client import VLMClient
from .vision import CameraFrames

log = logging.getLogger("wavego-pet")


class PetBrain:

    def __init__(self, supervisor: MotionSupervisor, vlm: VLMClient,
                 frames: CameraFrames, persona: PetPersona | None = None,
                 clock=time.monotonic, scene_every_s: float = 12.0,
                 on_event=None):
        self.supervisor = supervisor
        self.vlm = vlm
        self.frames = frames
        self.persona = persona or PetPersona()
        self._clock = clock
        self.scene_every_s = scene_every_s
        self._last_scene_t = -1e9
        self._on_event = on_event or (lambda e: log.info("%s", e))

    def tick(self) -> bool:
        """One brain cycle. True when a scene was processed (commands may
        have been submitted); False = skipped (rate limit / no frame)."""
        now = self._clock()
        if now - self._last_scene_t < self.scene_every_s:
            return False
        jpeg = self.frames.latest_jpeg()
        if jpeg is None:
            return False
        scene = self.vlm.describe(jpeg)
        if scene is None:
            return False
        self._last_scene_t = now
        self._dispatch(self.persona.update(scene, now), scene)
        return True

    def _dispatch(self, raw_commands: list[dict], scene: Scene) -> None:
        for raw in raw_commands:
            try:
                ticket = self.supervisor.submit(parse_command(raw))
                self._on_event({"event": "pet_command", "state": self.persona.state.value,
                                "scene": {"person": scene.person_visible,
                                          "position": scene.position,
                                          "gesture": scene.gesture},
                                "ticket": ticket.id, "action": ticket.action})
            except CommandRejected as exc:
                log.error("persona emitted an invalid command: %s", exc.reason)
            except QueueFullError:
                log.warning("motion queue full; dropping pet command")
            except EStopActive:
                log.warning("estop active; pet is staying put")
                return
