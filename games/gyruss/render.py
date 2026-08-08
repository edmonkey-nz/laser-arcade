"""Turn the Gyruss world into a scene. Every entity is a handful of points and
the sim caps how many enemies/bullets/explosions can be live at once, so a
frame never needs many shapes -- important since this is a busy-looking genre
on a real screen but has to stay sparse for the laser.
"""
from __future__ import annotations

import math

from engine import font
from engine.config import Settings
from engine.game import Scene

from . import world as W
from .world import GyrussWorld, GState

# Local ship template: nose at -x (points inward, toward the centre) when
# placed at world angle theta and rotated by theta (local +x = world outward).
SHIP = [(-0.075, 0.0), (0.048, 0.038), (0.014, 0.0), (0.048, -0.038), (-0.075, 0.0)]
DIAMOND = [(0.0, 1.0), (0.72, 0.0), (0.0, -1.0), (-0.72, 0.0), (0.0, 1.0)]
CENTRE_MARK = [(0.0, 0.05), (0.035, 0.0), (0.0, -0.05), (-0.035, 0.0), (0.0, 0.05)]


def _polar(r, a):
    return (r * math.cos(a), r * math.sin(a))


def _rotate(shape, ang, ox, oy, scale=1.0):
    c, s = math.cos(ang), math.sin(ang)
    out = []
    for (x, y) in shape:
        x, y = x * scale, y * scale
        out.append((ox + x * c - y * s, oy + x * s + y * c))
    return out


def _ring(r, n=20):
    return [_polar(r, k / n * math.tau) for k in range(n + 1)]


def _burst(cx, cy, r, n=6):
    return [(cx + math.cos(a) * r, cy + math.sin(a) * r)
            for a in [k / n * math.tau for k in range(n + 1)]]


def scene(world: GyrussWorld, cfg: Settings, t: float) -> Scene:
    out: Scene = []
    cring = cfg.beam(cfg.col_debris)
    cship = cfg.beam(cfg.col_ship)
    cform = cfg.beam(cfg.col_debris)
    cdive = cfg.beam(cfg.col_saucer)
    cbullet = cfg.beam(cfg.col_bullet)
    ctext = cfg.beam(cfg.col_text)

    if world.state == GState.PLAY:
        out.append((_ring(W.R_PLAYER), cring))
        out.append((CENTRE_MARK, cring))

        blink_hidden = world.invuln > 0 and int(t * 10) % 2 == 1
        if not blink_hidden:
            px, py = _polar(W.R_PLAYER, world.player_angle)
            out.append((_rotate(SHIP, world.player_angle, px, py, 1.0), cship))

        for b in world.bullets:
            x0, y0 = _polar(b.r, b.angle)
            x1, y1 = _polar(max(0.0, b.r - 0.05), b.angle)
            out.append(([(x0, y0), (x1, y1)], cbullet))

        for e in world.enemies:
            ex, ey = _polar(e.r, e.angle)
            scale = 0.028 + 0.020 * (e.r / W.R_PLAYER)
            colour = cdive if e.diving else cform
            out.append((_rotate(DIAMOND, 0.0, ex, ey, scale), colour))

        for ex in world.explosions:
            r = ex.radius()
            if r > 0.006:
                out.append((_burst(ex.x, ex.y, r), cdive))

    _hud(world, cfg, out, t, ctext, cdive, cship)
    return out


def _hud(world, cfg, out, t, ctext, caccent, cship):
    for pl in font.text_polylines(str(world.score), -0.95, 0.86, 0.06):
        out.append((pl, ctext))
    for pl in font.text_polylines("WAVE %d" % world.wave, 0.0, 0.86, 0.055, center=True):
        out.append((pl, ctext))
    # lives as small ship icons, top-right
    for i in range(min(world.lives, 5)):
        ix = 0.98 - i * 0.075
        out.append((_rotate(SHIP, math.pi, ix, 0.885, 0.55), cship))

    if world.state == GState.READY:
        for pl in font.text_polylines("GYRUSS", 0.0, 0.50, 0.14, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("FIRE TO START", 0.0, -0.15, 0.075, center=True):
                out.append((pl, ctext))
        for pl in font.text_polylines("LEFT RIGHT ORBIT  SPACE FIRE", 0.0, -0.4, 0.045, center=True):
            out.append((pl, ctext))
    elif world.state == GState.DEAD:
        for pl in font.text_polylines("SHIP LOST", 0.0, 0.30, 0.12, center=True):
            out.append((pl, caccent))
        for pl in font.text_polylines("SCORE " + str(world.score), 0.0, 0.06, 0.08, center=True):
            out.append((pl, ctext))
        for pl in font.text_polylines("WAVE " + str(world.wave), 0.0, -0.08, 0.06, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("FIRE TO RETRY", 0.0, -0.32, 0.07, center=True):
                out.append((pl, ctext))
