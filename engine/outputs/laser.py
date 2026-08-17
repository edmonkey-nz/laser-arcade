"""The seam between laser-arcade's point pipeline and the shared safety layer.

The arcade plans frames as a list of `(x, y, r, g, b)` 5-tuples already in DAC
units (see `engine.pathplan`). The shared backends in `laser_output.py`,
`helios.py` and `lasercube_output.py` -- copied verbatim from the upstream
laser-laser-laser repo, do not fork them -- speak a numpy `(N,6)` int32 frame
instead. `to_frame()` is the conversion, and it is also where the DAC-only
keystone warp now lives.

That relocation matters. The warp used to sit inside the Helios backend's
`send()`, i.e. *downstream* of everything. Now that a brightness ceiling exists,
any output transform must run BEFORE `SafeOutput.write()`, because anything
after the ceiling can exceed it (PORTING.md 4, "the one hard rule").
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np


def to_frame(points: Sequence[Tuple[int, int, int, int, int]], cfg) -> np.ndarray:
    """laser-arcade LaserPoint list -> (N,6) int32 for SafeOutput.write().

    Columns are x, y, r, g, b, i. `i` is `max(r, g, b)`, which is this repo's
    long-standing convention -- changing it would alter apparent brightness on
    the rig for no reason anyone would later remember.

    The keystone warp is applied here, so it lands on the DAC copy only; the
    on-screen simulator is fed the untouched `points` list and therefore never
    moves when you tune the projector.
    """
    n = len(points)
    if n == 0:
        return np.zeros((0, 6), dtype=np.int32)

    a = np.asarray(points, dtype=np.int32)        # (N,5): x, y, r, g, b
    frame = np.empty((n, 6), dtype=np.int32)
    frame[:, 2:5] = a[:, 2:5]
    frame[:, 5] = a[:, 2:5].max(axis=1)

    kh = float(getattr(cfg, "keystone_h", 0.0) or 0.0)
    kv = float(getattr(cfg, "keystone_v", 0.0) or 0.0)
    rng = int(getattr(cfg, "dac_range", 4095))

    if kh == 0.0 and kv == 0.0:
        frame[:, 0:2] = a[:, 0:2]
    else:
        c = rng / 2.0
        nx = (a[:, 0] - c) / c
        ny = (a[:, 1] - c) / c
        # Trapezoid pre-distortion about the field centre: widen/narrow x by
        # height (kh), then y by width (kv). The second line deliberately uses
        # the already-warped nx -- that is what the original per-point loop did,
        # and matching it exactly keeps every rig's saved calibration valid.
        nx = nx * (1.0 + kh * ny)
        ny = ny * (1.0 + kv * nx)
        xy = np.empty((n, 2), dtype=np.float64)
        xy[:, 0] = c + nx * c
        xy[:, 1] = c + ny * c
        # int() truncates toward zero and so does astype(int32); clip after,
        # matching the original's order of operations.
        frame[:, 0:2] = np.clip(xy.astype(np.int32), 0, rng)

    # The Helios field is 12-bit and the old backend masked rather than clipped.
    # Keep masking so behaviour is identical for any point already in range.
    frame[:, 0:2] &= 0x0FFF
    return frame


# -- Helios shared-library discovery ----------------------------------------
#
# Carried over from the old engine/outputs/helios.py. laser-arcade ships
# PyInstaller --onefile with the .so deliberately NOT bundled -- the user drops
# it next to the executable -- so injecting lib_path is load-bearing here in a
# way it is not for the other projects.

def _search_dirs() -> List[str]:
    """Where to look for the shared library, most specific first: next to the
    executable/script, the project root, the current directory, ~/.local/lib."""
    dirs: List[str] = []
    frozen = getattr(sys, "frozen", False)
    if frozen:
        # In a PyInstaller build the user drops the library beside the
        # executable. sys.executable is that path; sys.argv[0] can be a bare
        # name depending on how it was launched.
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    try:
        dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    except Exception:
        pass
    if not frozen:
        # Meaningless when frozen: __file__ then points inside the temporary
        # extraction directory, not at anything the user has.
        dirs.append(str(Path(__file__).resolve().parents[2]))
    dirs.append(os.getcwd())
    dirs.append(os.path.expanduser("~/.local/lib"))
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_helios_lib(cfg) -> Optional[str]:
    """First candidate that actually loads, or None to let the canonical
    backend fall back to its own next-to-helios.py default (which in turn lets
    the system loader try LD_LIBRARY_PATH / ldconfig)."""
    for d in _search_dirs():
        for name in getattr(cfg, "helios_libs", ()):
            p = os.path.join(d, os.path.basename(name))
            if not os.path.isfile(p):
                continue
            try:
                ctypes.cdll.LoadLibrary(p)
            except OSError:
                continue
            return p
    return None


def helios_help(cfg) -> str:
    """The diagnostic the old backend printed when it could not find the .so.
    Worth keeping: it is the single most common setup failure on a new rig."""
    names = ", ".join(sorted({os.path.basename(n)
                              for n in getattr(cfg, "helios_libs", ())}))
    where = ("next to the laser-arcade executable"
             if getattr(sys, "frozen", False) else "next to run.py")
    return ("[helios] shared library not found (%s).\n"
            "[helios] looked in: %s\n"
            "[helios] copy libHeliosDacAPI.so %s (see TECHNICAL.md "
            "'Helios DAC setup')." % (names, "; ".join(_search_dirs()), where))


# -- backend construction ----------------------------------------------------

KINDS = ("none", "helios", "lasercube")


def make_backend(kind: str, cfg):
    """Build a LaserOutput backend. Raises on failure so SafeOutput.swap_backend
    can report it and land on NullOutput.

    A literal if/elif with direct imports, not importlib: PyInstaller cannot
    trace importlib and would silently drop the backend from the bundle.
    """
    if kind == "helios":
        from helios import HeliosOutput
        lib = find_helios_lib(cfg)
        if lib is None:
            print(helios_help(cfg))
        return HeliosOutput(cfg.dac_device, lib_path=lib)
    if kind == "lasercube":
        from lasercube_output import LaserCubeOutput
        return LaserCubeOutput(ip=(cfg.lasercube_ip or None),
                               point_order=cfg.lasercube_point_order,
                               dry_run=cfg.lasercube_dry_run)
    from laser_output import NullOutput
    return NullOutput()
