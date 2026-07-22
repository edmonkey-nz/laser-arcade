"""Defender sound set."""
from __future__ import annotations

import numpy as np

from engine import audio as a


def build_sounds():
    sounds = {
        "fire": a.blip(700, 0.05, "square"),
        "kill": a.bang(0.25, 140),
        "hit": a.bang(0.45, 90),
        "capture": a.chirp(500, 200, 0.3, "square") * 0.6,
        "rescue": np.concatenate([a.blip(f, 0.07, "square") for f in (660, 880)]),
        "human_lost": a.bang(0.35, 60),
        "wave": np.concatenate([a.blip(f, 0.09, "square") for f in (523, 659, 784)]),
        "gameover": a.bang(0.9, 45),
    }
    return sounds, {}
