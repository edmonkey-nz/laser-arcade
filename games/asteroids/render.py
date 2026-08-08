"""Turn the live Asteroids World into a scene (world-space polylines + colour)."""
from __future__ import annotations

import math

from engine import font
from engine.config import Settings
from engine.game import Scene
from engine.vec import Vec2

from . import shapes as geo
from .world import State, World


def _place(shape, pos: Vec2, scale: float, angle: float):
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for (x, y) in shape:
        sx, sy = x * scale, y * scale
        out.append((pos.x + sx * c - sy * s, pos.y + sx * s + sy * c))
    return out


def scene(world: World, cfg: Settings, t: float) -> Scene:
    out: Scene = []

    ca = cfg.beam(cfg.col_asteroid)
    for a in world.asteroids:
        out.append((_place(geo.ASTEROIDS[a.shape_index], a.pos, a.radius, a.angle), ca))

    if world.ship is not None:
        blink_hidden = world.ship.invuln > 0 and int(t * 8) % 2 == 1
        if not blink_hidden:
            cs = cfg.beam(cfg.col_ship)
            out.append((_place(geo.SHIP, world.ship.pos, 0.06, world.ship.angle), cs))
            if world.ship.thrusting and int(t * 30) % 2 == 0:
                out.append((_place(geo.SHIP_FLAME, world.ship.pos, 0.06, world.ship.angle), cs))

    cb = cfg.beam(cfg.col_bullet)
    for b in world.bullets:
        out.append((_place(geo.BULLET, b.pos, 1.6, b.vel.angle()), cb))

    if world.saucer is not None:
        cu = cfg.beam(cfg.col_saucer)
        for stroke in geo.SAUCER:
            out.append((_place(stroke, world.saucer.pos, world.saucer.radius, 0.0), cu))
    csb = cfg.beam(cfg.col_saucer)
    for b in world.saucer_bullets:
        out.append((_place(geo.BULLET, b.pos, 1.6, b.vel.angle()), csb))

    cd = cfg.beam(cfg.col_debris)
    for d in world.debris:
        half = d.len / 2.0
        out.append((_place([(-half, 0.0), (half, 0.0)], d.pos, 1.0, d.angle), cd))

    _hud(world, cfg, t, out)
    return out


def _hud(world: World, cfg: Settings, t: float, out: Scene) -> None:
    ct = cfg.beam(cfg.col_text)
    for pl in font.text_polylines(str(world.score), -0.95, 0.80, 0.11):
        out.append((pl, ct))
    if world.high_score > 0:
        for pl in font.text_polylines(str(world.high_score), 0.0, 0.86, 0.06, center=True):
            out.append((pl, ct))
    cs = cfg.beam(cfg.col_ship)
    for i in range(min(world.lives, 5)):
        p = Vec2(-0.92 + i * 0.075, 0.66)
        out.append((_place(geo.SHIP, p, 0.03, math.pi / 2), cs))

    if world.state == State.ATTRACT:
        for pl in font.text_polylines("LASER ASTEROIDS", 0.0, 0.18, 0.15, center=True):
            out.append((pl, ct))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("PRESS FIRE", 0.0, -0.15, 0.09, center=True):
                out.append((pl, ct))
    elif world.state == State.GAMEOVER:
        for pl in font.text_polylines("GAME OVER", 0.0, 0.05, 0.15, center=True):
            out.append((pl, ct))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("PRESS FIRE", 0.0, -0.25, 0.08, center=True):
                out.append((pl, ct))
