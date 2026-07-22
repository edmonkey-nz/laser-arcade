"""Asteroids as a Laser Arcade game: maps the shell's InputState onto the
Asteroids simulation and exposes its scene and sounds."""
from __future__ import annotations

from typing import List, Set

import pygame

from engine.game import Game, InputState, Scene

from . import render, sfx
from .world import Input, World

_SAUCER_CUES = {"saucer_big", "saucer_small", "saucer_gone"}


class AsteroidsGame(Game):
    name = "ASTEROIDS"
    key = "asteroids"
    players = 1
    blurb = "ARROWS MOVE  SPACE FIRE  SHIFT HYPERSPACE"
    icon = [[(0.0, 0.9), (0.55, 0.55), (0.85, 0.05), (0.5, -0.5), (0.1, -0.35),
             (-0.45, -0.85), (-0.85, -0.2), (-0.55, 0.35), (-0.15, 0.35), (0.0, 0.9)]]

    def start(self) -> None:
        self.world = World()

    def update(self, dt: float, inp: InputState) -> None:
        km = self.cfg.keymap
        turn = 0
        if km.down(inp, "left"):
            turn += 1
        if km.down(inp, "right"):
            turn -= 1
        ai = Input(
            turn=turn,
            thrust=km.down(inp, "up"),
            fire=km.down(inp, "fire"),
            hyperspace=km.hit(inp, "alt"),
            start=inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER),
        )
        self.world.update(dt, ai)

    def scene(self, t: float) -> Scene:
        return render.scene(self.world, self.cfg, t)

    def sound_spec(self):
        return sfx.build_sounds()

    def audio_events(self) -> List[str]:
        return [e for e in self.world.events if e not in _SAUCER_CUES]

    def active_loops(self) -> Set[str]:
        loops: Set[str] = set()
        w = self.world
        if w.ship is not None and w.ship.thrusting:
            loops.add("thrust")
        if w.saucer is not None:
            loops.add("saucer_small" if w.saucer.small else "saucer_big")
        return loops


__all__ = ["AsteroidsGame"]
