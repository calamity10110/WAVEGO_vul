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
from .systemstats import SystemStats
from .vlm_client import VLMClient
from .vision import CameraFrames

log = logging.getLogger("wavego-pet")


class PetBrain:

    def __init__(self, supervisor: MotionSupervisor, vlm: VLMClient,
                 frames: CameraFrames, persona: PetPersona | None = None,
                 clock=time.monotonic, scene_every_s: float = 12.0,
                 on_event=None, display=None, validator=None, stats=None,
                 change_detector=None):
        self.supervisor = supervisor
        self.vlm = vlm
        self.frames = frames
        self.persona = persona or PetPersona()
        self.display = display            # optional LCDDisplay/NullDisplay
        self.validator = validator        # optional second opinion
        self.stats = stats                # optional SystemStats
        self.change_detector = change_detector  # optional scene-change gate
        self._clock = clock
        self.scene_every_s = scene_every_s
        self._last_scene_t = -1e9
        self._on_event = on_event or (lambda e: log.info("%s", e))

    def tick(self) -> bool:
        """One brain cycle. True when a scene was processed (commands may
        have been submitted); False = skipped (rate limit / no frame /
        scene unchanged)."""
        now = self._clock()
        if now - self._last_scene_t < self.scene_every_s:
            return False
        jpeg = self.frames.latest_jpeg()
        if jpeg is None:
            return False
        if self.change_detector is not None \
                and not self.change_detector.changed_jpeg(jpeg):
            return False                   # static scene: save the inference
        scene = self.vlm.describe(jpeg)
        if scene is None:
            return False
        if self.validator is not None:
            scene = self.validator.confirm(scene, jpeg)
        self._last_scene_t = now
        self._dispatch(self.persona.update(scene, now), scene)
        self._refresh_display(scene)
        return True

    def warmup(self, wait_for_frame_s: float = 5.0, poll_s: float = 0.1) -> None:
        """Precalculate: one throwaway describe at boot so llama-server's
        prompt cache holds our exact system+user prefix (and the model
        weights/vision encoder are paged in). The camera's first frame
        lands on its capture thread a moment after start(), so poll
        briefly for it. Failures are fine."""
        try:
            deadline = self._clock() + wait_for_frame_s
            jpeg = self.frames.latest_jpeg()
            for _ in range(200):            # cap: a frozen test clock must not hang us
                if jpeg is not None or self._clock() >= deadline:
                    break
                time.sleep(poll_s)
                jpeg = self.frames.latest_jpeg()
            if jpeg is not None:
                self.vlm.describe(jpeg)
                self._on_event({"event": "warmup_done"})
        except Exception as exc:
            log.debug("warmup failed (harmless): %s", exc)

    def _refresh_display(self, scene: Scene) -> None:
        if self.display is None:
            return
        try:
            extra = ""
            if self.supervisor.telemetry is not None:
                voltage = self.supervisor.telemetry.poll().voltage_v
                if voltage:
                    extra = f"battery {voltage:.1f} V"
            stats_text = SystemStats.format(self.stats.poll()) \
                if self.stats is not None else ""
            self.display.update(
                state=self.persona.state.value,
                scene=("sees: person {} ({})".format(
                    scene.position, scene.gesture)
                    if scene.person_visible else "sees: nothing"),
                extra=extra,
                stats_text=stats_text)
        except Exception as exc:
            log.debug("display refresh failed: %s", exc)

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
