"""MISSILE COMMAND as a Laser Arcade game. Mouse-driven: aim with the cursor,
click to fire a counter-missile."""
from __future__ import annotations

from typing import List

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import MissileInput, MissileWorld, MState


class MissileGame(Game):
    name = "MISSILE"
    key = "missile"
    players = 1
    blurb = "MOUSE/STICK AIM  FIRE"
    icon = [
        [(-0.9, 0.0), (-0.35, 0.0)], [(0.35, 0.0), (0.9, 0.0)],
        [(0.0, -0.9), (0.0, -0.35)], [(0.0, 0.35), (0.0, 0.9)],
        [(-0.08, -0.75), (0.08, -0.75), (0.08, -0.15), (0.0, 0.05), (-0.08, -0.15), (-0.08, -0.75)],
    ]

    def start(self) -> None:
        self.world = MissileWorld()

    def update(self, dt: float, inp: InputState) -> None:
        start = inp.mouse_click or inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER)
        mi = MissileInput(aim=inp.mouse_pos, fire=inp.mouse_click, start=start)
        self.world.update(dt, mi)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def score(self):
        return self.world.score

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return list(self.world.events)


__all__ = ["MissileGame"]
