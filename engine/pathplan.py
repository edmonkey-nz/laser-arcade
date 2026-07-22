"""Turn a scene (world-space polylines + colours) into a stream of laser points.

A galvo scanner is a physical thing: the mirrors take time to move and settle.
Good-looking laser graphics come almost entirely from how you *sequence* the
points, not the shapes themselves. This planner does the four things that matter:

  1. Interpolate long segments so brightness is even and lines stay straight
     (a big gap between two lit points = a fast, faint, bowed line).
  2. Dwell (repeat points) at the start, end and corners so the mirrors arrive
     and settle before the beam does something important.
  3. Blank (beam off) while slewing between disconnected shapes, and slew in
     small steps so the mirrors actually track to the destination -- otherwise
     you get a bright "tail" when the beam switches back on mid-flight.
  4. Adaptively coarsen density when a frame gets busy, to hold the frame rate.

Output points are (x, y, r, g, b) with x, y in DAC units (0..4095).
"""
from __future__ import annotations

import math
from typing import List, Tuple

from .config import Settings

LaserPoint = Tuple[int, int, int, int, int]
BLANK = (0, 0, 0)


class Mapper:
    """Maps world coordinates [-1, 1] onto the DAC's 0..range square."""

    def __init__(self, s: Settings):
        self.s = s
        self.center = s.dac_range / 2.0
        self.half = self.center * s.fill

    def __call__(self, p: Tuple[float, float]) -> Tuple[int, int]:
        x, y = p
        s = self.s
        if s.swap_xy:
            x, y = y, x
        if s.invert_x:
            x = -x
        if s.invert_y:
            y = -y
        dx = self.center + x * self.half
        dy = self.center + y * self.half
        r = s.dac_range
        ix = 0 if dx < 0 else r if dx > r else int(dx)
        iy = 0 if dy < 0 else r if dy > r else int(dy)
        return (ix, iy)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _interp(stream, p0, p1, colour, step, include_start=False):
    """Emit lit points from p0 to p1 spaced <= `step` apart."""
    d = _dist(p0, p1)
    n = max(1, int(math.ceil(d / step)))
    r, g, b = colour
    start = 0 if include_start else 1
    for i in range(start, n + 1):
        t = i / n
        x = int(p0[0] + (p1[0] - p0[0]) * t)
        y = int(p0[1] + (p1[1] - p0[1]) * t)
        stream.append((x, y, r, g, b))


def _blank_move(stream, p0, p1, step):
    """Slew from p0 to p1 with the beam off, in small tracking steps."""
    d = _dist(p0, p1)
    n = max(1, int(math.ceil(d / step)))
    for i in range(1, n + 1):
        t = i / n
        x = int(p0[0] + (p1[0] - p0[0]) * t)
        y = int(p0[1] + (p1[1] - p0[1]) * t)
        stream.append((x, y, 0, 0, 0))


def plan(scene: List[Tuple[List[Tuple[float, float]], Tuple[int, int, int]]],
         s: Settings) -> Tuple[List[LaserPoint], float]:
    """Build the laser point stream for one frame.

    Returns (points, effective_step). `effective_step` is handy for the HUD /
    debugging so you can see when adaptive coarsening kicks in.
    """
    mp = Mapper(s)
    center = (int(mp.center), int(mp.center))

    # 1. Map everything to DAC space; normalise dots to 2-point segments.
    polys: List[Tuple[List[Tuple[int, int]], Tuple[int, int, int]]] = []
    for poly_w, colour in scene:
        if not poly_w:
            continue
        if len(poly_w) == 1:
            p = mp(poly_w[0])
            polys.append(([p, p], colour))
            continue
        polys.append(([mp(p) for p in poly_w], colour))

    if not polys:
        return [center + BLANK], float(s.max_step)

    # 2. Choose an effective step to hold a stable frame rate. The DAC plays a
    #    frame in numPoints / pps seconds, so the honest budget is the *total*
    #    point count for one frame at the target rate -- not just the lit ones.
    #    We subtract the fixed per-shape overhead (dwell + blanked travel) and
    #    spend whatever's left on interpolation, never finer than max_step.
    total_len = 0.0
    for dac, _ in polys:
        for a, b in zip(dac, dac[1:]):
            total_len += _dist(a, b)

    target_total = min(s.dac_max_points, max(s.lit_budget,
                       int(s.pps / max(1, s.target_fps) * 0.95)))
    per_poly_fixed = s.blank_dwell + s.start_dwell + s.end_dwell
    fixed = 0
    for dac, _ in polys:
        fixed += per_poly_fixed + max(0, len(dac) - 2) * s.corner_dwell
    # rough blanked-travel overhead: one modest jump per shape
    fixed += int(len(polys) * (s.dac_range * 0.35) / s.blank_step)

    interp_budget = max(len(polys), target_total - fixed)
    step = float(s.max_step)
    if total_len > 0:
        step = max(step, total_len / interp_budget)

    # 3. Greedy nearest-neighbour ordering from the current beam position, to
    #    keep blanked travel (and therefore flicker) down.
    remaining = list(range(len(polys)))
    ordered: List[int] = []
    cur = center
    while remaining:
        best_i = min(remaining, key=lambda i: _dist(cur, polys[i][0][0]))
        ordered.append(best_i)
        cur = polys[best_i][0][-1]
        remaining.remove(best_i)

    # 4. Emit the stream.
    stream: List[LaserPoint] = []
    cur = center
    for idx in ordered:
        dac, colour = polys[idx]
        start = dac[0]
        _blank_move(stream, cur, start, s.blank_step)
        for _ in range(s.blank_dwell):
            stream.append((start[0], start[1], 0, 0, 0))
        for _ in range(s.start_dwell):
            stream.append((start[0], start[1], *colour))
        prev = start
        last = len(dac) - 1
        for i in range(1, len(dac)):
            pt = dac[i]
            _interp(stream, prev, pt, colour, step)
            if i != last:
                for _ in range(s.corner_dwell):
                    stream.append((pt[0], pt[1], *colour))
            prev = pt
        for _ in range(s.end_dwell):
            stream.append((prev[0], prev[1], *colour))
        cur = prev

    # 5. Respect the DAC's hard per-frame limit.
    if len(stream) > s.dac_max_points:
        stream = stream[:s.dac_max_points]
    if not stream:
        stream = [center + BLANK]
    return stream, step
