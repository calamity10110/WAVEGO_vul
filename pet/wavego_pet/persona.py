"""The pet persona — a deterministic mood/behavior engine.

Takes scenes from the VLM (what the robot sees) and emits semantic command
dicts for the motion supervisor. Pure logic, no I/O, clock-injected: fully
testable, and every command it emits is clamped to pet-safe bounds BEFORE
the supervisor's validator sees it.

Behavior rules (pet-like, safety-first):
- Sleeping: wakes (stand + lean up) when a person appears; otherwise silent.
- Greeting: person appears -> short forward walk + lean up + (jump if the
  trick cooldown allows). Greeting is rate-limited; a pet that pounces every
  tick is a hazard, not a pet.
- Playing: wave gesture -> trick (jump + spin), on a cooldown.
- Following: person visible -> short slow bursts; turn toward them when
  off-center, tiny forward walk when centered.
- Idle fidgets: nobody around -> glance left/right now and then, and sit
  down to sleep after a long quiet stretch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PetState(str, Enum):
    SLEEPING = "sleeping"
    IDLE = "idle"
    GREETING = "greeting"
    FOLLOWING = "following"
    PLAYING = "playing"


@dataclass(frozen=True)
class Scene:
    """What the VLM says about the current frame."""
    person_visible: bool = False
    position: str = "none"    # left | center | right | none
    gesture: str = "none"     # wave | point | none

    @staticmethod
    def nothing() -> "Scene":
        return Scene()


# Pet-safe clamps — the persona NEVER emits anything beyond these, whatever
# it is asked to do. The supervisor's validator is the second gate.
MAX_WALK_DURATION_S = 2.5
MAX_WALK_SPEED = 0.5
MAX_TURN_DURATION_S = 1.5


@dataclass
class PersonaConfig:
    greet_cooldown_s: float = 60.0
    trick_cooldown_s: float = 30.0
    follow_cooldown_s: float = 5.0
    follow_walk_s: float = 1.2
    follow_speed: float = 0.35
    greet_walk_s: float = 1.2
    greet_speed: float = 0.4
    greet_trick: bool = True          # jump as part of the greeting
    fidget_after_s: float = 45.0      # idle this long -> glance around
    sleep_after_s: float = 300.0      # idle this long -> sit and sleep
    follow_turn_s: float = 0.6        # blind turn toward an off-center person


class PetPersona:

    def __init__(self, cfg: PersonaConfig | None = None, clock=None):
        import time

        self.cfg = cfg or PersonaConfig()
        self._clock = clock or time.monotonic
        self.state = PetState.IDLE
        self._person_visible_prev = False
        # boot counts as "seen just now": a pet switched on in an empty room
        # stays curious for a while before dozing off. Greeting and tricks
        # use the epoch sentinel instead so the FIRST person still gets the
        # full welcome, no matter how soon they show up.
        boot = self._clock()
        self._last_greet = -1e9
        self._last_trick = -1e9
        self._last_follow = boot
        self._last_fidget = boot
        self._last_person_seen = boot
        self._fidget_side = 1

    # ------------------------------------------------------------- helpers

    def _clamp_walk(self, duration: float, speed: float) -> tuple[float, float]:
        return (min(duration, MAX_WALK_DURATION_S),
                min(speed, MAX_WALK_SPEED))

    def _walk(self, direction: str, duration: float, speed: float) -> dict:
        d, s = self._clamp_walk(duration, speed)
        return {"action": "walk", "direction": direction, "duration": d,
                "speed": s}

    def _turn(self, direction: str) -> dict:
        return {"action": "turn", "direction": direction,
                "duration": min(self.cfg.follow_turn_s, MAX_TURN_DURATION_S)}

    @staticmethod
    def _lean(pitch: float = 0.0, yaw: float = 0.0) -> dict:
        """Body-lean expression (no gimbal on this robot): pitch + = nose
        up, yaw + = nose left, in firmware body degrees (pitch <= 17.5,
        yaw <= 30)."""
        return {"action": "lean", "pitch": pitch, "yaw": yaw}

    # ---------------------------------------------------------------- tick

    def update(self, scene: Scene, now: float | None = None) -> list[dict]:
        """Feed one scene, get the semantic commands the pet wants now.
        Returns [] most ticks — pets mostly just hang around."""
        if now is None:
            now = self._clock()

        # GREETING/PLAYING/FOLLOWING are single-shot bursts: they were
        # observable for the tick that triggered them; revert before this one.
        if self.state in (PetState.GREETING, PetState.PLAYING, PetState.FOLLOWING):
            self.state = PetState.IDLE

        commands: list[dict] = []
        appeared = scene.person_visible and not self._person_visible_prev
        if scene.person_visible:
            self._last_person_seen = now
        self._person_visible_prev = scene.person_visible

        if self.state is PetState.SLEEPING:
            if scene.person_visible:
                self.state = PetState.IDLE
                self._last_person_seen = now
                return [{"action": "stand"}, self._lean(pitch=10.0)]
            return []

        if self.state is PetState.IDLE:
            commands = self._update_idle(scene, appeared, now)
        return commands

    def _update_idle(self, scene: Scene, appeared: bool, now: float) -> list[dict]:
        cfg = self.cfg

        if appeared and now - self._last_greet >= cfg.greet_cooldown_s:
            self._last_greet = now
            self.state = PetState.GREETING
            cmds = [self._walk("forward", cfg.greet_walk_s, cfg.greet_speed),
                    self._lean(pitch=10.0)]
            if cfg.greet_trick and now - self._last_trick >= cfg.trick_cooldown_s:
                self._last_trick = now
                cmds.append({"action": "jump"})
            return cmds

        if scene.gesture == "wave" and now - self._last_trick >= cfg.trick_cooldown_s:
            self._last_trick = now
            self.state = PetState.PLAYING
            return [{"action": "jump"},
                    {"action": "turn", "direction": "left",
                     "duration": MAX_TURN_DURATION_S}]

        if scene.person_visible and now - self._last_follow >= cfg.follow_cooldown_s:
            self._last_follow = now
            self.state = PetState.FOLLOWING
            if scene.position == "left":
                return [self._turn("left")]
            if scene.position == "right":
                return [self._turn("right")]
            return [self._walk("forward", cfg.follow_walk_s, cfg.follow_speed)]

        idle_for = now - self._last_person_seen
        if idle_for >= cfg.sleep_after_s:
            self.state = PetState.SLEEPING
            return [{"action": "sit"}]
        if idle_for >= cfg.fidget_after_s and now - self._last_fidget >= cfg.fidget_after_s:
            self._last_fidget = now
            self._fidget_side *= -1
            return [self._lean(yaw=20.0 * self._fidget_side)]

        return []
