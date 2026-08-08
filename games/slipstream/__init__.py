"""SLIPSTREAM as a Laser Arcade game."""
from __future__ import annotations

from typing import List, Set

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import RacerInput, RacerWorld, RState


class SlipstreamGame(Game):
    name = "SLIPSTREAM"
    key = "slipstream"
    players = 1
    blurb = "LEFT/RIGHT STEER  UP GAS  DOWN DRIFT  R RETRY"
    icon = [
        [(-0.9, -0.9), (-0.15, 0.75)],
        [(0.9, -0.9), (0.15, 0.75)],
        [(-0.18, -0.35), (0.18, -0.35)],
        [(-0.06, 0.1), (0.06, 0.1)],
    ]

    def start(self) -> None:
        self.world = RacerWorld(level=0)

    def update(self, dt: float, inp: InputState) -> None:
        km = self.cfg.keymap
        steer = (1 if km.down(inp, "right") else 0) - (1 if km.down(inp, "left") else 0)
        ri = RacerInput(
            steer=float(steer),
            accel=km.down(inp, "up"),
            brake=km.down(inp, "brake"),
            start=inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER) or km.hit(inp, "fire"),
            retry=km.hit(inp, "retry"),
        )
        self.world.update(dt, ri)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return list(self.world.events)

    def active_loops(self) -> Set[str]:
        w = self.world
        loops: Set[str] = set()
        if w.state == RState.RACE:
            f = w.speed_frac()
            loops.add("thrust_lo" if f < 0.35 else "thrust_mid" if f < 0.72 else "thrust_hi")
            if w.scraping:
                loops.add("scrape")
        return loops


__all__ = ["SlipstreamGame"]
