"""Snake sound set."""
from __future__ import annotations

from engine import audio as a


def build_sounds():
    sounds = {
        "eat": a.blip(700, 0.05, "square"),
        "crash": a.bang(0.5, 70),
    }
    return sounds, {}
