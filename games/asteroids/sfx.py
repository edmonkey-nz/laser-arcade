"""Asteroids sound set, built from the engine's synth primitives."""
from __future__ import annotations

from engine import audio as a


def build_sounds():
    """Return (one_shots, loops) as name -> mono float arrays."""
    sounds = {
        "fire": a.chirp(900, -2900, 0.16, "square") * 0.6,
        "bang_large": a.bang(0.5, 90),
        "bang_med": a.bang(0.35, 140),
        "bang_small": a.bang(0.22, 220),
        "ship_explode": a.bang(0.7, 70),
        "extra_life": _extra_life(),
        "saucer_fire": a.chirp(700, -1800, 0.14, "square") * 0.5,
        "beat_hi": a.blip(160, 0.14, "square"),
        "beat_lo": a.blip(120, 0.14, "square"),
    }
    loops = {
        "thrust": a.rumble(70, 0.4),
        "saucer_big": a.warble(70, 130, 0.5),
        "saucer_small": a.warble(150, 240, 0.5),
    }
    return sounds, loops


def _extra_life():
    import numpy as np
    parts = [a.blip(f, 0.09, "square") for f in (660, 880, 1320)]
    return np.concatenate(parts)
