"""Project the SLIPSTREAM track into a receding wireframe scene.

The track ahead is sampled segment by segment; each sample is projected with a
simple 1/(1+d) perspective onto the [-1, 1] screen. Curvature accumulates so
bends sweep across the view; altitude lifts the road; ramps leave gaps in the
rails. The craft sits near the bottom centre and banks with the stick.
"""
from __future__ import annotations

import math

from engine import font
from engine.config import Settings
from engine.game import Scene

from . import world as W
from .world import RacerWorld, RState

# craft seen from behind: a hover chevron with a notched tail
SHIP = [(-0.11, -0.05), (0.0, 0.10), (0.11, -0.05),
        (0.055, -0.03), (0.0, 0.015), (-0.055, -0.03), (-0.11, -0.05)]
SHIP_FIN = [(0.0, 0.015), (0.0, 0.075)]


def _fmt(t: float) -> str:
    return "%0.2f" % t


def _place(shape, ox, oy, roll, sx=1.0, sy=1.0):
    c, s = math.cos(roll), math.sin(roll)
    out = []
    for (x, y) in shape:
        x, y = x * sx, y * sy
        out.append((ox + x * c - y * s, oy + x * s + y * c))
    return out


def scene(world: RacerWorld, cfg: Settings, t: float) -> Scene:
    out: Scene = []
    crail = cfg.beam(cfg.col_ship)
    crung = cfg.beam(cfg.col_debris)
    ctext = cfg.beam(cfg.col_text)
    caccent = cfg.beam(cfg.col_saucer)

    # horizon (title screen only -- it's a distraction while racing)
    if world.state == RState.READY:
        out.append(([(-1.05, W.HORIZON_Y), (1.05, W.HORIZON_Y)], crung))

    tk = world.track
    samples = []          # (idx, cx, y, w, gap)
    obstacles = []        # (ox, y, sz)
    if world.state not in (RState.FINISH, RState.DEAD):
        base = int(world.pos)
        fract = world.pos - base
        dx = 0.0
        xx = 0.0
        alt = 0.0
        for i in range(W.DRAW):
            idx = base + i
            if idx >= tk.length:
                break
            dx += tk.curve[idx]
            xx += dx
            alt += tk.hill[idx]
            d = i - fract
            denom = 1.0 + d * W.CAM_K
            if denom <= 1e-3:
                continue
            p = 1.0 / denom
            y = W.HORIZON_Y - (W.HORIZON_Y - W.NEAR_Y) * p + alt * W.HILL_Y * p
            cx = (xx * W.CURVE_X - world.player_x * W.ROADW_NEAR) * p
            w = W.ROADW_NEAR * p
            samples.append((idx, cx, y, w, tk.gap[idx]))
            lat = tk.obstacle[idx]
            if lat is not None and not tk.gap[idx]:
                obstacles.append((cx + lat * w, y, 0.20 * w))

    # rails, broken into runs at gaps
    cur_l, cur_r = [], []
    for (idx, cx, y, w, gap) in samples:
        if gap:
            if len(cur_l) > 1:
                out.append((cur_l, crail))
            if len(cur_r) > 1:
                out.append((cur_r, crail))
            cur_l, cur_r = [], []
            continue
        cur_l.append((cx - w, y))
        cur_r.append((cx + w, y))
    if len(cur_l) > 1:
        out.append((cur_l, crail))
    if len(cur_r) > 1:
        out.append((cur_r, crail))

    # rungs + finish band
    for (idx, cx, y, w, gap) in samples:
        if gap:
            continue
        if idx >= tk.length - 3:
            out.append(([(cx - w, y), (cx + w, y)], caccent))
        elif idx % W.RUNG_SPACING == 0:
            out.append(([(cx - w, y), (cx + w, y)], crung))

    # obstacles: upright warning diamonds sitting on the track (far ones first)
    for (ox, y, sz) in obstacles:
        out.append(([(ox, y + sz * 1.9), (ox + sz, y + sz * 0.7), (ox, y),
                     (ox - sz, y + sz * 0.7), (ox, y + sz * 1.9)], caccent))

    _ship(world, cfg, out, t, crail, caccent)
    _hud(world, cfg, out, t, ctext, caccent)
    return out


def _ship(world, cfg, out, t, crail, caccent):
    if world.state in (RState.FINISH, RState.DEAD):
        return
    # flash when the hull is critical
    if world.health_frac() < 0.28 and int(t * 8) % 2 == 0:
        return
    air = world.airborne > 0.0
    lift = 0.0
    scale = 1.0
    if air and W.AIR_TIME > 0:
        f = world.air_t / W.AIR_TIME
        lift = math.sin(math.pi * max(0.0, min(1.0, f))) * 0.16
        scale = 1.0 + lift * 0.8
    ox = 0.0 + world.lean * 0.06
    oy = -0.80 + lift
    roll = -world.lean * 0.5
    out.append((_place(SHIP, ox, oy, roll, scale, scale), crail))
    out.append((_place(SHIP_FIN, ox, oy, roll, scale, scale), crail))
    # scrape sparks on the offending side
    if world.scraping and int(t * 30) % 2 == 0:
        side = math.copysign(1.0, world.player_x)
        sx = 0.16 * side
        out.append(([(sx, oy - 0.02), (sx + 0.05 * side, oy + 0.03)], caccent))
        out.append(([(sx, oy + 0.01), (sx + 0.06 * side, oy - 0.01)], caccent))


def _hud(world, cfg, out, t, ctext, caccent):
    # time (left), best (right), level (centre-top)
    for pl in font.text_polylines("TIME " + _fmt(world.time), -0.95, 0.86, 0.055):
        out.append((pl, ctext))
    best = world.best_time()
    for pl in font.text_polylines("BEST " + (_fmt(best) if best else "--"),
                                  0.95, 0.86, 0.055):
        # right-align by shifting left of the anchor
        w = font.text_width("BEST " + (_fmt(best) if best else "--")) * (0.055 / 6.0)
        out.append(([(px - w, py) for (px, py) in pl], ctext))
    for pl in font.text_polylines("LV %d" % (world.level + 1), 0.0, 0.86, 0.055, center=True):
        out.append((pl, ctext))

    # speed: a single line growing from the left, bottom-left
    x0, x1, yb = -0.95, -0.55, -0.955
    fx = x0 + (x1 - x0) * max(0.0, min(1.0, world.speed_frac()))
    out.append(([(x0, yb), (max(fx, x0 + 0.002), yb)], caccent))

    # hull: a single line growing from the right, bottom-right; red when low
    hx1, hx0 = 0.95, 0.55
    hf = world.health_frac()
    hfx = hx1 - (hx1 - hx0) * hf
    hcol = caccent if hf < 0.35 else cfg.beam(cfg.col_ship)
    out.append(([(min(hfx, hx1 - 0.002), yb), (hx1, yb)], hcol))

    if world.state == RState.READY:
        for pl in font.text_polylines("SLIPSTREAM", 0.0, 0.52, 0.12, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("PRESS ENTER", 0.0, -0.2, 0.085, center=True):
                out.append((pl, ctext))
        for pl in font.text_polylines("UP GAS  DOWN DRIFT", 0.0, -0.45, 0.05, center=True):
            out.append((pl, ctext))
    elif world.state == RState.FINISH:
        for pl in font.text_polylines("FINISH", 0.0, 0.40, 0.13, center=True):
            out.append((pl, caccent))
        for pl in font.text_polylines("TIME " + _fmt(world.time), 0.0, 0.14, 0.09, center=True):
            out.append((pl, ctext))
        best = world.best_time()
        for pl in font.text_polylines("BEST " + (_fmt(best) if best else "--"),
                                      0.0, -0.02, 0.07, center=True):
            out.append((pl, ctext))
        if world.new_best and int(t * 3) % 2 == 0:
            for pl in font.text_polylines("NEW BEST", 0.0, -0.18, 0.07, center=True):
                out.append((pl, caccent))
        for pl in font.text_polylines("ENTER NEXT   R RETRY", 0.0, -0.42, 0.055, center=True):
            out.append((pl, ctext))
    elif world.state == RState.DEAD:
        # explosion burst where the craft was
        cx, cy = 0.0, -0.55
        for k in range(9):
            a = k / 9.0 * math.tau
            r0 = 0.05 + 0.03 * (k % 3)
            r1 = 0.22 + 0.05 * (k % 4)
            out.append(([(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                         (cx + math.cos(a) * r1, cy + math.sin(a) * r1)], caccent))
        for pl in font.text_polylines("DESTROYED", 0.0, 0.30, 0.14, center=True):
            out.append((pl, caccent))
        for pl in font.text_polylines("HULL BREACHED", 0.0, 0.08, 0.06, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("ENTER RETRY", 0.0, -0.14, 0.08, center=True):
                out.append((pl, ctext))
