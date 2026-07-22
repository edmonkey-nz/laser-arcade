"""Gyruss sound set."""
from __future__ import annotations

import numpy as np

from engine import audio as a


def build_sounds():
    sounds = {
        "fire": a.chirp(760, 1400, 0.08, "square") * 0.6,
        "kill": a.blip(880, 0.06, "square"),
        "hit": a.bang(0.4, 90),
        "wave": np.concatenate([a.blip(f, 0.09, "square") for f in (523, 659, 784)]),
        "gameover": a.bang(0.9, 45),
    }
    return sounds, {}
