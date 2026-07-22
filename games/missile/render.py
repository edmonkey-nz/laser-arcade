"""Turn the Missile Command world into a scene. Kept deliberately sparse: every
shape (city, missile trail, explosion) is a handful of points, and the sim caps
how many missiles/explosions can be live at once, so a frame never gets heavy.
"""
from __future__ import annotations

import math

from engine import font
from engine.config import Settings
from engine.game import Scene

from . import world as W
from .world import MissileWorld, MState

CITY = [(-0.05, 0.0), (-0.05, 0.045), (-0.022, 0.07), (0.022, 0.07),
        (0.05, 0.045), (0.05, 0.0), (-0.05, 0.0)]
RUBBLE = [(-0.045, 0.0), (-0.01, 0.018), (0.02, 0.0), (0.045, 0.014)]
LAUNCHER = [(-0.05, 0.0), (0.0, 0.07), (0.05, 0.0)]


def _explosion_poly(cx, cy, r, n=8):
    return [(cx + math.cos(a) * r, cy + math.sin(a) * r)
            for a in [k / n * math.tau for k in range(n + 1)]]


def scene(world: MissileWorld, cfg: Settings, t: float) -> Scene:
    out: Scene = []
    cground = cfg.beam(cfg.col_debris)
    ccity = cfg.beam(cfg.col_ship)
    crubble = cfg.beam(cfg.col_debris)
    cenemy = cfg.beam(cfg.col_saucer)
    cplayer = cfg.beam(cfg.col_bullet)
    cblast = cfg.beam(cfg.col_saucer)
    ctext = cfg.beam(cfg.col_text)

    # ground
    out.append(([(-1.05, W.GROUND_Y), (1.05, W.GROUND_Y)], cground))

    if world.state in (MState.PLAY, MState.BREAK):
        # launcher
        out.append(([(W.LAUNCH_POS[0] + x, W.LAUNCH_POS[1] + y) for (x, y) in LAUNCHER], ccity))

        # cities: house outline if alive, a flat rubble mark if lost
        for i, x in enumerate(W.CITY_XS):
            if world.cities[i]:
                out.append(([(x + px, W.GROUND_Y + py) for (px, py) in CITY], ccity))
            else:
                out.append(([(x + px, W.GROUND_Y + py) for (px, py) in RUBBLE], crubble))

    if world.state == MState.PLAY:
        # enemy missiles: origin -> current, as a short streak
        for m in world.enemies:
            out.append(([(m.ox, m.oy), (m.x, m.y)], cenemy))
        # player missiles
        for m in world.players:
            out.append(([(m.ox, m.oy), (m.x, m.y)], cplayer))
        # explosions
        for ex in world.explosions:
            r = ex.radius()
            if r > 0.005:
                out.append((_explosion_poly(ex.x, ex.y, r), cblast))

        # crosshair (two full lines -- cheaper than four short segments)
        cx, cy = world.cursor
        s = 0.045
        out.append(([(cx - s, cy), (cx + s, cy)], ctext))
        out.append(([(cx, cy - s), (cx, cy + s)], ctext))

    _hud(world, cfg, out, t, ctext, cblast)
    return out


def _hud(world, cfg, out, t, ctext, caccent):
    for pl in font.text_polylines(str(world.score), -0.95, 0.86, 0.06):
        out.append((pl, ctext))
    for pl in font.text_polylines("WAVE %d" % world.wave, 0.0, 0.86, 0.055, center=True):
        out.append((pl, ctext))

    if world.state == MState.READY:
        for pl in font.text_polylines("MISSILE COMMAND", 0.0, 0.50, 0.10, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("CLICK TO START", 0.0, -0.15, 0.075, center=True):
                out.append((pl, ctext))
        for pl in font.text_polylines("AIM AND CLICK TO FIRE", 0.0, -0.4, 0.045, center=True):
            out.append((pl, ctext))
    elif world.state == MState.BREAK:
        for pl in font.text_polylines("WAVE %d" % world.wave, 0.0, 0.05, 0.16, center=True):
            out.append((pl, caccent))
    elif world.state == MState.DEAD:
        for pl in font.text_polylines("CITIES LOST", 0.0, 0.30, 0.12, center=True):
            out.append((pl, caccent))
        for pl in font.text_polylines("SCORE " + str(world.score), 0.0, 0.06, 0.08, center=True):
            out.append((pl, ctext))
        for pl in font.text_polylines("WAVE " + str(world.wave), 0.0, -0.08, 0.06, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("CLICK TO RETRY", 0.0, -0.32, 0.07, center=True):
                out.append((pl, ctext))
