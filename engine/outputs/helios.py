"""Helios Laser DAC backend.

Talks to the Helios shared library (libHeliosDacAPI.so / libHeliosLaserDAC.so)
through ctypes, and drives it from its own thread so the game loop never blocks
on USB. Frames are single-buffered here: `send()` just stashes the newest frame
and the writer pushes it as soon as the DAC reports ready. That "latest wins"
policy is exactly right for a game -- if we ever fall behind, we want the
current frame, not a stale queued one.

The DAC itself is double-buffered and loops the last frame it received, so a
brief stall shows the previous frame rather than going dark.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .base import Output

# Helios flag bits (see HeliosDacAPI.h). We poll GetStatus ourselves, so the
# default (blocking-when-full) behaviour is fine.
HELIOS_FLAGS_DEFAULT = 0


class HeliosPoint(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("x", ctypes.c_uint16),   # 0..4095 (12-bit)
        ("y", ctypes.c_uint16),   # 0..4095
        ("r", ctypes.c_uint8),
        ("g", ctypes.c_uint8),
        ("b", ctypes.c_uint8),
        ("i", ctypes.c_uint8),    # intensity / master brightness
    ]


class HeliosOutput(Output):
    def __init__(self, lib_names, device: int = 0, max_points: int = 4096,
                 settings=None):
        self.lib_names = lib_names
        self.device = device
        self.max_points = max_points
        self.settings = settings          # for output-only pincushion correction
        self.lib = None

        self._lock = threading.Lock()
        self._pending: Optional[Tuple[object, int, int]] = None  # (array, n, pps)
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # -- library loading ----------------------------------------------------
    def _search_dirs(self) -> List[str]:
        """Where to look for the shared library, most specific first: next to the
        executable/script, the project root, the current directory,
        ~/.local/lib."""
        dirs: List[str] = []
        frozen = getattr(sys, "frozen", False)
        if frozen:
            # In a PyInstaller build the user drops the library beside the
            # executable. sys.executable is that path; sys.argv[0] can be a
            # bare name depending on how it was launched.
            dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
        try:
            dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
        except Exception:
            pass
        if not frozen:
            # Meaningless when frozen: __file__ then points inside the
            # temporary extraction directory, not at anything the user has.
            dirs.append(str(Path(__file__).resolve().parents[2]))
        dirs.append(os.getcwd())
        dirs.append(os.path.expanduser("~/.local/lib"))
        seen, out = set(), []
        for d in dirs:
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def _candidates(self) -> List[str]:
        """Full list of things to hand to LoadLibrary: explicit paths in each
        search dir, then the bare names so the system loader can find installed
        copies via LD_LIBRARY_PATH / ldconfig."""
        out, seen = [], set()
        for d in self._search_dirs():
            for name in self.lib_names:
                p = os.path.join(d, os.path.basename(name))
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        for name in self.lib_names:            # bare names -> system loader
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _load(self) -> bool:
        for name in self._candidates():
            # for explicit paths, skip quietly if the file isn't there
            if os.path.sep in name and not os.path.isfile(name):
                continue
            try:
                lib = ctypes.cdll.LoadLibrary(name)
            except OSError:
                continue
            try:
                lib.OpenDevices.restype = ctypes.c_int
                lib.GetStatus.argtypes = [ctypes.c_uint]
                lib.GetStatus.restype = ctypes.c_int
                lib.WriteFrame.argtypes = [
                    ctypes.c_uint, ctypes.c_uint, ctypes.c_uint8,
                    ctypes.POINTER(HeliosPoint), ctypes.c_uint,
                ]
                lib.WriteFrame.restype = ctypes.c_int
                lib.Stop.argtypes = [ctypes.c_uint]
                lib.Stop.restype = ctypes.c_int
                lib.CloseDevices.restype = ctypes.c_int
            except AttributeError:
                continue
            self.lib = lib
            self._lib_name = name
            return True
        return False

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        if not self._load():
            names = ", ".join(sorted({os.path.basename(n) for n in self.lib_names}))
            print(f"[helios] shared library not found ({names}).")
            print("[helios] looked in: " + "; ".join(self._search_dirs()))
            where = ("next to the laser-arcade executable"
                     if getattr(sys, "frozen", False) else "next to run.py")
            print("[helios] copy libHeliosDacAPI.so %s "
                  "(see TECHNICAL.md 'Helios DAC setup'). If you had it in your "
                  "old laser-asteroids folder, just copy it across." % where)
            return False
        n = self.lib.OpenDevices()
        if n <= 0:
            print(f"[helios] {self._lib_name} loaded but no DAC found "
                  f"(OpenDevices returned {n}). Check USB / udev rules.")
            return False
        # let it settle; GetStatus can be not-ready on the first tries
        for _ in range(100):
            if self.lib.GetStatus(self.device) == 1:
                break
            time.sleep(0.005)
        print(f"[helios] connected via {self._lib_name}; {n} device(s).")
        self._running = True
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()
        return True

    def send(self, points: List[Tuple[int, int, int, int, int]], pps: int) -> None:
        n = min(len(points), self.max_points)
        arr = (HeliosPoint * n)()
        # Output-only distortion correction. This warps what the DAC scans; the
        # on-screen simulator is fed the same `points` list untouched, so the
        # preview never moves when you tune the projector.
        s = self.settings
        kh = getattr(s, "keystone_h", 0.0) if s else 0.0
        kv = getattr(s, "keystone_v", 0.0) if s else 0.0
        rng = getattr(s, "dac_range", 4095) if s else 4095
        warp = kh != 0.0 or kv != 0.0
        c = rng / 2.0
        for idx in range(n):
            x, y, r, g, b = points[idx]
            if warp:
                nx = (x - c) / c
                ny = (y - c) / c
                # trapezoid pre-distortion: widen/narrow x by height (kh),
                # widen/narrow y by width (kv), about the field centre.
                nx = nx * (1.0 + kh * ny)
                ny = ny * (1.0 + kv * nx)
                x = int(c + nx * c)
                y = int(c + ny * c)
                x = 0 if x < 0 else rng if x > rng else x
                y = 0 if y < 0 else rng if y > rng else y
            p = arr[idx]
            p.x = x & 0x0FFF
            p.y = y & 0x0FFF
            p.r = r
            p.g = g
            p.b = b
            p.i = max(r, g, b)
        with self._lock:
            self._pending = (arr, n, int(pps))

    def _writer(self) -> None:
        while self._running:
            frame = None
            with self._lock:
                if self._pending is not None:
                    frame = self._pending
                    self._pending = None
            if frame is None:
                time.sleep(0.0005)
                continue
            arr, n, pps = frame
            # wait for the DAC to be ready to accept the new frame
            while self._running:
                status = self.lib.GetStatus(self.device)
                if status == 1:
                    break
                time.sleep(0.0003)
            if not self._running:
                break
            try:
                self.lib.WriteFrame(self.device, pps, HELIOS_FLAGS_DEFAULT, arr, n)
            except Exception as e:  # pragma: no cover
                print("[helios] WriteFrame failed:", e)
                self._running = False
                break

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self.lib is not None:
            try:
                self.lib.Stop(self.device)
                self.lib.CloseDevices()
            except Exception:
                pass
