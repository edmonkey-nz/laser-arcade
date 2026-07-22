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

    def _px(self, x: int, y: int) -> Tuple[int, int]:
        # The invert/swap flags are *hardware calibration* for the projector, so
        # we undo them here: the preview should always show the upright,
        # audience-correct image regardless of how the beam had to be flipped for
        # your optics. Fill/scale are kept, so the field margin still shows.
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
