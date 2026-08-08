"""SNAKE as a Laser Arcade game."""
from __future__ import annotations

from typing import List

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import SnakeInput, SnakeWorld, SState


class SnakeGame(Game):
    name = "SNAKE"
    key = "snake"
    players = 1
    blurb = "ARROWS STEER"
    icon = [
        [(-0.85, 0.5), (-0.3, 0.5), (-0.3, -0.1), (0.3, -0.1), (0.3, 0.5), (0.7, 0.5)],
        [(0.78, -0.55), (0.92, -0.4), (0.78, -0.25), (0.64, -0.4), (0.78, -0.55)],
    ]

    def start(self) -> None:
        self.world = SnakeWorld()

    def update(self, dt: float, inp: InputState) -> None:
        km = self.cfg.keymap
        dir_ = None
        if km.down(inp, "up"):
            dir_ = "up"
        elif km.down(inp, "down"):
            dir_ = "down"
        elif km.down(inp, "left"):
            dir_ = "left"
        elif km.down(inp, "right"):
            dir_ = "right"
        si = SnakeInput(
            dir=dir_,
            start=inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER) or km.hit(inp, "fire"),
        )
        self.world.update(dt, si)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def score(self):
        return self.world.score

    def set_high_score(self, value: int) -> None:
        self.world.best = value

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return list(self.world.events)


__all__ = ["SnakeGame"]
