"""Remappable gameplay controls.

Games ask the keymap about named *actions* ("left", "accel", ...) instead of
raw key codes, so the config screen can rebind them. Reserved shell keys (Esc,
Q, P, Enter) are deliberately not remappable.
"""
from __future__ import annotations

from typing import Dict, List

import pygame

# (action key, human label) -- order is the order shown in the config screen
ACTIONS = [
    ("left", "LEFT"),
    ("right", "RIGHT"),
    ("up", "ACCEL / THRUST"),
    ("down", "DOWN / DESCEND"),
    ("brake", "SLIPSTREAM BRAKE"),
    ("fire", "FIRE / SERVE"),
    ("alt", "HYPERSPACE"),
    ("retry", "RETRY"),
    ("p1_up", "PONG P1 UP"),
    ("p1_down", "PONG P1 DN"),
    ("p2_up", "PONG P2 UP"),
    ("p2_down", "PONG P2 DN"),
]

DEFAULT_KEYS: Dict[str, List[int]] = {
    "left": [pygame.K_LEFT, pygame.K_a],
    "right": [pygame.K_RIGHT, pygame.K_d],
    "up": [pygame.K_UP, pygame.K_w],
    "down": [pygame.K_DOWN, pygame.K_s],
    "brake": [pygame.K_DOWN, pygame.K_s],
    "fire": [pygame.K_SPACE],
    "alt": [pygame.K_LSHIFT, pygame.K_h],
    "retry": [pygame.K_r],
    "p1_up": [pygame.K_w],
    "p1_down": [pygame.K_s],
    "p2_up": [pygame.K_UP],
    "p2_down": [pygame.K_DOWN],
}


class KeyMap:
    def __init__(self, bindings: Dict[str, List[int]] = None):
        self.bindings = {a: list(DEFAULT_KEYS[a]) for a, _ in ACTIONS}
        if bindings:
            for a, ks in bindings.items():
                if a in self.bindings and ks:
                    self.bindings[a] = [int(k) for k in ks]

    def down(self, inp, *actions: str) -> bool:
        return any(k in inp.held for a in actions for k in self.bindings.get(a, ()))

    def hit(self, inp, *actions: str) -> bool:
        return any(k in inp.pressed for a in actions for k in self.bindings.get(a, ()))

    def label(self, action: str) -> str:
        """Short label for the config list: the primary bound key only."""
        ks = self.bindings.get(action, ())
        if not ks:
            return "--"
        return pygame.key.name(ks[0]).upper()

    def rebind(self, action: str, keycode: int) -> None:
        if action in self.bindings:
            self.bindings[action] = [int(keycode)]

    def reset(self) -> None:
        self.bindings = {a: list(DEFAULT_KEYS[a]) for a, _ in ACTIONS}

    def to_dict(self) -> Dict[str, List[int]]:
        return {a: list(ks) for a, ks in self.bindings.items()}
