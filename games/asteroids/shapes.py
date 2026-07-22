"""Asteroids vector artwork. Local unit space; scaled/rotated into the world."""
from __future__ import annotations

import math
from typing import List, Tuple

Polyline = List[Tuple[float, float]]

# Ship, drawn pointing along +X (angle 0). Classic concave-tail triangle.
SHIP: Polyline = [
    (1.1, 0.0), (-0.7, 0.62), (-0.42, 0.0), (-0.7, -0.62), (1.1, 0.0),
]

# Exhaust flame, drawn behind the tail when thrusting.
SHIP_FLAME: Polyline = [(-0.42, 0.28), (-1.05, 0.0), (-0.42, -0.28)]


def _blob(seed_radii: List[float]) -> Polyline:
    """Closed jagged rock loop of unit-ish radius from per-vertex radii."""
    n = len(seed_radii)
    pts: Polyline = []
    for i, r in enumerate(seed_radii):
        a = (i / n) * math.tau
        pts.append((math.cos(a) * r, math.sin(a) * r))
    pts.append(pts[0])
    return pts


ASTEROIDS: List[Polyline] = [
    _blob([1.0, 0.75, 1.0, 0.6, 0.95, 1.0, 0.7, 1.0, 0.8, 1.0]),
    _blob([0.9, 1.0, 0.7, 1.0, 0.85, 0.6, 1.0, 0.75, 1.0, 0.9]),
    _blob([1.0, 0.85, 0.65, 1.0, 0.9, 1.0, 0.7, 0.95, 1.0, 0.8, 0.95]),
    _blob([0.8, 1.0, 0.9, 0.7, 1.0, 0.85, 1.0, 0.6, 0.95, 1.0]),
]

# Flying saucer: hull (hex) + dome + waist line.
SAUCER: List[Polyline] = [
    [(-1.0, 0.0), (-0.45, 0.35), (0.45, 0.35), (1.0, 0.0),
     (0.45, -0.35), (-0.45, -0.35), (-1.0, 0.0)],
    [(-0.45, 0.35), (-0.22, 0.72), (0.22, 0.72), (0.45, 0.35)],
    [(-1.0, 0.0), (1.0, 0.0)],
]

# Bullet: a very short streak (two close points = a bright dot).
BULLET: Polyline = [(-0.02, 0.0), (0.02, 0.0)]
