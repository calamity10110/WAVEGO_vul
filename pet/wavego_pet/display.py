"""Optional DSI LCD status display (the WAVEGO kit's small screen).

The Pi 5 exposes two MIPI connectors (DSI0/DSI1-labeled); a DSI display
plugged into either shows up under /sys/class/drm as cardN-DSI-*. The
status UI draws the pet's state and what it last saw, using pygame over
the KMS/DRM backend (no X server needed). Everything here degrades
gracefully: no display, no pygame, or a broken DRM setup just disables
the UI — the pet runs fine without it.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

log = logging.getLogger("wavego-pet")


def detect_dsi_display(listdir: Callable[[str], list] = os.listdir) -> str | None:
    """Return the DRM connector name of a DSI display (e.g.
    'card1-DSI-1'), or None when none is attached (DSI0 or DSI1 both
    match)."""
    try:
        names = listdir("/sys/class/drm")
    except OSError:
        return None
    for name in sorted(names):
        if name.startswith("card") and "DSI" in name:
            return name
    return None


class NullDisplay:
    """No-op stand-in (mock mode, or no screen attached)."""

    def start(self) -> None: ...

    def update(self, state: str, scene: str = "", extra: str = "") -> None: ...

    def stop(self) -> None: ...

    @property
    def active(self) -> bool:
        return False


class LCDDisplay:
    """Fullscreen status card on the DSI LCD: pet name, current state,
    last scene summary, system stats (CPU/RAM/temp), extra line. The
    layout scales to the configured display size/resolution."""

    def __init__(self, width: int = 480, height: int = 480,
                 title: str = "WAVEGO PET"):
        self.width = width
        self.height = height
        self.title = title
        self._pygame = None
        self._screen = None
        self._fonts: list = []
        self._active = False

    def layout(self) -> dict:
        """Font pixel sizes + row geometry, proportional to the panel."""
        h, w = self.height, self.width
        return {
            "title": max(16, int(h * 0.10)),
            "state": max(14, int(h * 0.085)),
            "text": max(11, int(h * 0.055)),
            "stats": max(11, int(h * 0.05)),
            "row": h // 6,
            "pad": max(8, w // 30),
        }

    def start(self) -> None:
        try:
            if not os.environ.get("DISPLAY"):
                # systemd service has no X; SDL speaks to the DRM backend
                os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
            import pygame

            pygame.init()
            self._screen = pygame.display.set_mode((self.width, self.height))
            pygame.font.init()
            lay = self.layout()
            self._fonts = [pygame.font.SysFont(None, lay["title"]),
                           pygame.font.SysFont(None, lay["state"]),
                           pygame.font.SysFont(None, lay["text"]),
                           pygame.font.SysFont(None, lay["stats"])]
            self._pygame = pygame
            self._active = True
            log.info("status display on DSI LCD (%dx%d)", self.width,
                     self.height)
        except Exception as exc:
            log.warning("status display disabled: %s", exc)
            self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def update(self, state: str, scene: str = "", extra: str = "",
               stats_text: str = "") -> None:
        if not self._active:
            return
        try:
            pg = self._pygame
            lay = self.layout()
            self._screen.fill((13, 15, 20))
            rows = [
                (self.title, (240, 240, 240), self._fonts[0]),
                (state.upper(), (120, 220, 120), self._fonts[1]),
                (scene, (200, 200, 200), self._fonts[2]),
                (extra, (230, 200, 120), self._fonts[2]),
            ]
            y = lay["pad"]
            for text, color, font in rows:
                if text:
                    surf = font.render(str(text), True, color)
                    self._screen.blit(surf, (lay["pad"], y))
                y += lay["row"]
            if stats_text:
                surf = self._fonts[3].render(stats_text, True,
                                             (140, 180, 240))
                self._screen.blit(surf, (lay["pad"],
                                         self.height - lay["stats"] - 6))
            pg.display.flip()
        except Exception as exc:
            log.debug("display update failed: %s", exc)

    def stop(self) -> None:
        if self._pygame is not None:
            try:
                self._pygame.quit()
            except Exception:
                pass
            self._active = False
