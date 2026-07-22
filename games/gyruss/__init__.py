"""GYRUSS as a Laser Arcade game."""
from __future__ import annotations

from typing import List

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import GyrussInput, GyrussWorld, GState


class GyrussGame(Game):
    name = "GYRUSS"
    key = "gyruss"
    players = 1
    blurb = "LEFT RIGHT ORBIT  SPACE FIRE"
    icon = [
        [(0.85, 0.0), (0.6, 0.6), (0.0, 0.85), (-0.6, 0.6), (-0.85, 0.0),
         (-0.6, -0.6), (0.0, -0.85), (0.6, -0.6), (0.85, 0.0)],
        [(0.0, -0.85), (-0.18, -0.55), (0.05, -0.55), (0.18, -0.55), (0.0, -0.85)],
    ]

    def start(self) -> None:
        self.world = GyrussWorld()

    def update(self, dt: float, inp: InputState) -> None:
        km = self.cfg.keymap
        steer = (1 if km.down(inp, "right") else 0) - (1 if km.down(inp, "left") else 0)
        gi = GyrussInput(
            steer=float(steer),
            fire=km.down(inp, "fire"),
            start=inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER) or km.hit(inp, "fire"),
        )
        self.world.update(dt, gi)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return list(self.world.events)


__all__ = ["GyrussGame"]
