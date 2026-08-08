"""DEFENDER as a Laser Arcade game."""
from __future__ import annotations

from typing import List

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import DefenderInput, DefenderWorld, DState


class DefenderGame(Game):
    name = "DEFENDER"
    key = "defender"
    players = 1
    blurb = "ARROWS FLY  SPACE FIRE"
    icon = [
        [(-0.85, -0.6), (-0.4, -0.1), (0.0, -0.6), (0.4, 0.5), (0.85, -0.6)],
        [(0.15, 0.15), (0.75, 0.35), (0.35, 0.4), (0.15, 0.15)],
    ]

    def start(self) -> None:
        self.world = DefenderWorld()

    def update(self, dt: float, inp: InputState) -> None:
        km = self.cfg.keymap
        sx = (1 if km.down(inp, "right") else 0) - (1 if km.down(inp, "left") else 0)
        sy = (1 if km.down(inp, "up") else 0) - (1 if km.down(inp, "down") else 0)
        di = DefenderInput(
            steer_x=float(sx),
            steer_y=float(sy),
            fire=km.down(inp, "fire"),
            start=inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER) or km.hit(inp, "fire"),
        )
        self.world.update(dt, di)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def score(self):
        return self.world.score

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return list(self.world.events)


__all__ = ["DefenderGame"]
