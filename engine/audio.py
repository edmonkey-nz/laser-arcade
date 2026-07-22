"""Procedural audio, in the spirit of an arcade board: every sound is
synthesised from numpy at start-up, no sample files.

The synth primitives (env/noise/tone/chirp/bang/blip) are here for games to
build their own sound sets. `SoundBank` is game-agnostic: you hand it a dict of
named one-shots and a dict of named loops, then trigger one-shots by name and
declare which loops should be sounding each frame. If audio can't initialise
(e.g. headless) the whole thing degrades to no-ops.
"""
from __future__ import annotations

from typing import Dict, Set

import numpy as np

RATE = 44100

try:
    import pygame
    _HAVE_PYGAME = True
except Exception:  # pragma: no cover
    _HAVE_PYGAME = False


# --- synth primitives (return mono float arrays in [-1, 1]) ----------------
def env(n: int, attack: float = 0.005, release: float = 0.05) -> np.ndarray:
    """Attack/decay amplitude envelope of length n samples."""
    e = np.ones(n)
    a = max(1, int(attack * RATE))
    r = max(1, int(release * RATE))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] *= np.linspace(1, 0, r)
    return e


def noise(dur: float) -> np.ndarray:
    return np.random.uniform(-1, 1, int(dur * RATE))


def tone(freq, dur: float, kind: str = "sine") -> np.ndarray:
    """A tone. `freq` may be a constant or a function of a time array (for
    sweeps)."""
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    if callable(freq):
        phase = 2 * np.pi * np.cumsum(freq(t)) / RATE
    else:
        phase = 2 * np.pi * freq * t
    if kind == "square":
        return np.sign(np.sin(phase))
    if kind == "saw":
        return 2 * (phase / (2 * np.pi) % 1.0) - 1.0
    if kind == "tri":
        return 2 * np.abs(2 * (phase / (2 * np.pi) % 1.0) - 1) - 1
    return np.sin(phase)


def blip(freq: float, dur: float = 0.06, kind: str = "square") -> np.ndarray:
    """Short percussive beep -- the classic Pong-style blip."""
    n = int(dur * RATE)
    return tone(freq, dur, kind) * env(n, 0.001, dur * 0.6) * 0.9


def chirp(f0: float, f1: float, dur: float, kind: str = "square") -> np.ndarray:
    n = int(dur * RATE)
    return tone(lambda t: f0 + (f1 - f0) * t / dur, dur, kind) * env(n, 0.001, dur * 0.5)


def bang(dur: float, base: float) -> np.ndarray:
    """Noisy explosion with a falling pitched component."""
    n = int(dur * RATE)
    s = noise(dur) * 0.7 + tone(lambda t: base * (1 - 0.6 * t / dur), dur, "square") * 0.5
    return s * np.exp(-np.linspace(0, 4.5, n))


def rumble(freq: float = 70.0, dur: float = 0.4) -> np.ndarray:
    """Seamless low rumble: low-passed noise + a low saw (a thrust loop)."""
    n = int(dur * RATE)
    nse = noise(dur)
    y = np.zeros(n)
    a = 0.02
    for i in range(1, n):
        y[i] = y[i - 1] + a * (nse[i] - y[i - 1])
    y = y / (np.max(np.abs(y)) + 1e-9)
    return (y * 0.7 + tone(freq, dur, "saw") * 0.3) * 0.5


def warble(low: float, high: float, dur: float = 0.5) -> np.ndarray:
    """Two-tone warbling loop (a saucer)."""
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    w = low + (high - low) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 9 * t)))
    return np.sign(np.sin(2 * np.pi * np.cumsum(w) / RATE)) * 0.5


def _to_stereo_int16(mono: np.ndarray, volume: float) -> np.ndarray:
    mono = np.clip(mono, -1.0, 1.0) * (volume * 32767.0)
    data = mono.astype(np.int16)
    return np.column_stack((data, data))


class SoundBank:
    """Owns one-shot Sounds and looping channels for the currently-running game.

    Construct with dicts of name -> mono float array. Trigger one-shots with
    `play(name)`. Each frame, call `apply_loops(active_names)` with the set of
    loops that should currently be sounding; the bank starts/stops channels to
    match.
    """

    def __init__(self, sounds: Dict[str, np.ndarray] = None,
                 loops: Dict[str, np.ndarray] = None,
                 volume: float = 0.7, enabled: bool = True):
        self.enabled = enabled and _HAVE_PYGAME
        self.volume = volume
        self.sounds = {}
        self.loops = {}
        self._chans: Dict[str, "pygame.mixer.Channel"] = {}
        if not self.enabled:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.pre_init(RATE, -16, 2, 512)
                pygame.mixer.init()
            pygame.mixer.set_num_channels(24)
            self.sounds = {k: self._mk(v) for k, v in (sounds or {}).items()}
            self.loops = {k: self._mk(v) for k, v in (loops or {}).items()}
        except Exception:
            self.enabled = False

    def _mk(self, mono: np.ndarray):
        return pygame.sndarray.make_sound(_to_stereo_int16(mono, self.volume))

    def play(self, key: str) -> None:
        if not self.enabled:
            return
        snd = self.sounds.get(key)
        if snd:
            snd.play()

    def apply_loops(self, active: Set[str]) -> None:
        if not self.enabled:
            return
        # stop loops no longer wanted
        for name in list(self._chans):
            if name not in active:
                self._chans[name].stop()
                del self._chans[name]
        # start loops newly wanted
        for name in active:
            if name not in self._chans and name in self.loops:
                self._chans[name] = self.loops[name].play(loops=-1)

    def stop_all(self) -> None:
        if not self.enabled:
            return
        self.apply_loops(set())

    def close(self) -> None:
        self.stop_all()
