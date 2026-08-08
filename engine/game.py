"""The contract every game implements, plus the shared scene/input types.

The shell drives games generically: each frame it builds an `InputState`, calls
`update`, asks for a `scene` to draw, and handles audio via the game's declared
one-shot events and active loops. A game owns its own internal states (attract,
serving, game over, ...); the shell only knows menu-vs-game.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Set, Tuple

import numpy as np

Point = Tuple[float, float]
Polyline = List[Point]
Colour = Tuple[int, int, int]
# A scene is a list of (world-space polyline, colour) pairs. World space is the
# [-1, 1] square; the path planner maps it onto the DAC.
Scene = List[Tuple[Polyline, Colour]]

# Sound spec: dicts of name -> mono float32 samples. `sounds` are one-shots,
# `loops` are seamless loops the game switches on/off via active_loops().
SoundSpec = Tuple[dict, dict]


class InputState:
    """Immutable snapshot of the keyboard (and mouse) for one frame.

    `held` = keys currently down; `pressed` = keys that went down this frame
    (edges). Keys are pygame key constants; games use `down()`/`hit()` so they
    never poke the raw sets. `mouse_pos` is in world space ([-1, 1] square,
    matching the on-screen preview) or None if unknown; `mouse_down`/
    `mouse_click` mirror held/pressed but for the primary mouse button.
    """

    def __init__(self, held: Set[int], pressed: Set[int],
                 mouse_pos: Optional[Tuple[float, float]] = None,
                 mouse_down: bool = False, mouse_click: bool = False):
        self.held = held
        self.pressed = pressed
        self.mouse_pos = mouse_pos
        self.mouse_down = mouse_down
        self.mouse_click = mouse_click

    def down(self, *keys: int) -> bool:
        return any(k in self.held for k in keys)

    def hit(self, *keys: int) -> bool:
        return any(k in self.pressed for k in keys)


class Game:
    """Base class for a laser arcade game. Override what you need."""

    name: str = "GAME"          # menu label
    players: int = 1            # shown in the menu
    blurb: str = ""             # one short line of controls, shown in the menu
    # Small pictogram for the carousel menu: a list of polylines in local
    # space roughly bounded to [-1, 1]. The shell scales, positions and
    # colours it -- games just describe the shape. Empty = no icon drawn.
    icon: List[Polyline] = []

    def __init__(self, cfg):
        self.cfg = cfg

    def start(self) -> None:
        """Called once when the game is launched from the menu."""

    def update(self, dt: float, inp: InputState) -> None:
        """Advance the simulation by dt seconds."""

    def scene(self, t: float) -> Scene:
        """Return what to draw this frame. `t` is seconds since launch."""
        return []

    # --- high score (optional) --------------------------------------------
    def score(self) -> Optional[int]:
        """Current score, for the persistent high-score table. None means this
        game doesn't keep one -- two-player games have no single score, and a
        time trial's record is a *low* time, which this table can't express."""
        return None

    def set_high_score(self, value: int) -> None:
        """Seed the best score from previous sessions. Only games that show a
        high score need to do anything with it."""

    # --- audio (all optional) ---------------------------------------------
    def sound_spec(self) -> SoundSpec:
        """Return (one_shots, loops) as name -> mono float32 arrays. Built once
        when the game launches."""
        return ({}, {})

    def audio_events(self) -> List[str]:
        """One-shot sound names to trigger this frame."""
        return []

    def active_loops(self) -> Set[str]:
        """The set of loop names that should be sounding right now. The shell
        starts/stops loops to match, so mutually-exclusive loops are handled
        just by not naming more than one."""
        return set()
