"""Pong sound set: classic short blips, built from engine synth primitives."""
from __future__ import annotations

from engine import audio as a


def build_sounds():
    sounds = {
        "paddle": a.blip(520, 0.05, "square"),
        "wall": a.blip(320, 0.05, "square"),
        "serve": a.blip(660, 0.05, "square"),
        "score": a.chirp(300, 120, 0.22, "square"),
        "win": _fanfare(),
    }
    return sounds, {}


def _fanfare():
    import numpy as np
    return np.concatenate([a.blip(f, 0.10, "square") for f in (523, 659, 784, 1047)])
