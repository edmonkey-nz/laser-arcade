"""Missile Command sound set."""
from __future__ import annotations

import numpy as np

from engine import audio as a


def build_sounds():
    sounds = {
        "launch": a.blip(500, 0.06, "square"),
        "burst": a.bang(0.3, 130),
        "kill": a.blip(900, 0.05, "square"),
        "ground": a.bang(0.28, 80),
        "city_lost": a.bang(0.45, 55),
        "wave": np.concatenate([a.blip(f, 0.09, "square") for f in (523, 659, 784)]),
        "gameover": a.bang(0.9, 45),
    }
    return sounds, {}
