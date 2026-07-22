"""Two-player Pong as a Laser Arcade game."""
from __future__ import annotations

from typing import List

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import PongInput, PongWorld


class PongGame(Game):
    name = "PONG"
    key = "pong"
    players = 2
    blurb = "P1 W/S   P2 UP/DOWN   ENTER SERVE"
    icon = [
        [(-0.85, -0.5), (-0.65, -0.5), (-0.65, 0.5), (-0.85, 0.5), (-0.85, -0.5)],
        [(0.65, -0.5), (0.85, -0.5), (0.85, 0.5), (0.65, 0.5), (0.65, -0.5)],
        [(-0.08, 0.08), (0.08, 0.08), (0.08, -0.08), (-0.08, -0.08), (-0.08, 0.08)],
    ]

    def start(self) -> None:
        self.world = PongWorld()

    def update(self, dt: float, inp: InputState) -> None:
        km = self.cfg.keymap
        p1 = (1 if km.down(inp, "p1_up") else 0) - (1 if km.down(inp, "p1_down") else 0)
        p2 = (1 if km.down(inp, "p2_up") else 0) - (1 if km.down(inp, "p2_down") else 0)
        pi = PongInput(
            p1_dir=p1,
            p2_dir=p2,
            serve=km.hit(inp, "fire") or inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER),
        )
        self.world.update(dt, pi)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return list(self.world.events)


__all__ = ["PongGame"]
