"""SLIPSTREAM sounds: a hover-jet engine that rises in pitch with speed (three
loop buckets the shell crossfades between), a scrape loop for wall contact, and
one-shots for launching, landing and finishing.
"""
from __future__ import annotations

import numpy as np

from engine import audio as a


def _engine(freq: float) -> np.ndarray:
    """A jet-ish hum: stacked saws plus low-passed noise, loopable."""
    dur = 0.4
    n = int(dur * a.RATE)
    base = a.tone(freq, dur, "saw") * 0.5 + a.tone(freq * 1.5, dur, "saw") * 0.22
    nse = a.noise(dur)
    y = np.zeros(n)
    k = 0.05
    for i in range(1, n):
        y[i] = y[i - 1] + k * (nse[i] - y[i - 1])
    y /= (np.max(np.abs(y)) + 1e-9)
    return (base * 0.7 + y * 0.3) * 0.45


def _scrape() -> np.ndarray:
    """Bright, harsh noise for grinding along a wall."""
    dur = 0.25
    n = int(dur * a.RATE)
    nse = a.noise(dur)
    y = np.zeros(n)
    k = 0.25
    for i in range(1, n):
        y[i] = y[i - 1] + k * (nse[i] - y[i - 1])
    hp = nse - y                     # cheap high-pass
    return hp * 0.5


def build_sounds():
    sounds = {
        "launch": a.chirp(300, 900, 0.35, "saw"),
        "land": a.bang(0.22, 120),
        "bump": a.bang(0.16, 90),
        "crash": a.bang(0.4, 70),
        "explode": a.bang(0.75, 55),
        "finish": np.concatenate([a.blip(f, 0.12, "square")
                                  for f in (523, 659, 784, 1047, 1319)]),
    }
    loops = {
        "thrust_lo": _engine(70),
        "thrust_mid": _engine(110),
        "thrust_hi": _engine(165),
        "scrape": _scrape(),
    }
    return sounds, loops
