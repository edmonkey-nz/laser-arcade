"""On-screen preview of the laser output.

Crucially, this draws the *planned point stream* (already in DAC space), not the
game objects -- so what you see on screen is what the DAC receives, blanking
jumps and all. It's a faithful-enough proxy for framing, tuning dwell/step, and
playing without a laser attached.
"""
from __future__ import annotations

from typing import List, Tuple

import pygame

from .base import Output
from ..config import Settings


class Simulator(Output):
    def __init__(self, surface: "pygame.Surface", cfg: Settings):
        self.surface = surface
        self.cfg = cfg
        self.size = surface.get_width()
        self._status = None
        # The one place in the engine that uses a bitmap font rather than the
        # stroke font. Deliberate: the arm badge is an *operator* readout for
        # the monitor, and drawing it with the vector font would burn it into
        # the projected image and spend beam points on it.
        try:
            self._font = pygame.font.SysFont(None, 26, bold=True)
        except Exception:
            self._font = None

    def set_status(self, status) -> None:
        """(text, colour) drawn as a badge over the preview, or None."""
        self._status = status

    def _draw_status(self) -> None:
        if not self._status or self._font is None:
            return
        text, colour = self._status
        label = self._font.render(text, True, colour)
        pad = 6
        box = pygame.Surface((label.get_width() + pad * 2,
                              label.get_height() + pad * 2))
        box.set_alpha(190)
        box.fill((0, 0, 0))
        self.surface.blit(box, (8, 8))
        self.surface.blit(label, (8 + pad, 8 + pad))

    def _px(self, x: int, y: int) -> Tuple[int, int]:
        # The invert/swap flags are *hardware calibration* for the projector, so
        # we undo them here: the preview should always show the upright,
        # audience-correct image regardless of how the beam had to be flipped for
        # your optics. Fill and output_scale are deliberately NOT undone -- they
        # are framing rather than distortion correction, so the field margin and
        # a scaled-down image both show up here exactly as the DAC gets them.
        cfg = self.cfg
        half = (cfg.dac_range / 2.0) * cfg.fill
        nx = (x - cfg.dac_range / 2.0) / half
        ny = (y - cfg.dac_range / 2.0) / half
        if cfg.invert_x:
            nx = -nx
        if cfg.invert_y:
            ny = -ny
        if cfg.swap_xy:
            nx, ny = ny, nx
        c = self.size / 2.0
        hp = c * cfg.fill
        sx = c + nx * hp
        sy = c - ny * hp                      # world Y is up; screen Y is down
        return (int(sx), int(sy))

    def send(self, points: List[Tuple[int, int, int, int, int]], pps: int) -> None:
        cfg = self.cfg
        surf = self.surface
        prev = None
        prev_px = None
        for pt in points:
            x, y, r, g, b = pt
            px = self._px(x, y)
            lit = (r or g or b)
            if prev_px is not None:
                if lit:
                    colour = (min(r, 255), min(g, 255), min(b, 255))
                    if cfg.sim_glow:
                        dim = (colour[0] // 3, colour[1] // 3, colour[2] // 3)
                        pygame.draw.line(surf, dim, prev_px, px, 5)
                        pygame.draw.line(surf, dim, prev_px, px, 3)
                    pygame.draw.line(surf, colour, prev_px, px, 1)
                elif cfg.sim_show_blanking:
                    pygame.draw.line(surf, (30, 30, 30), prev_px, px, 1)
            if cfg.sim_show_points:
                surf.set_at(px, (60, 60, 90) if not lit else (r, g, b))
            prev = pt
            prev_px = px
        self._draw_status()
