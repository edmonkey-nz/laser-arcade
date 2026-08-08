"""Turn the Defender world into a scene. Only entities inside the visible
window become shapes -- the world can be arbitrarily wide, but a frame never
draws more than what's on screen. The ground is drawn from the real angular
terrain vertices only (see world.terrain_profile), so it reads as proper
straight-edged mountains rather than a densely-sampled curve.
"""
from __future__ import annotations

import math

from engine import font
from engine.config import Settings
from engine.game import Scene

from . import world as W
from .world import DefenderWorld, DState

SHIP_R = [(0.06, 0.0), (-0.04, 0.032), (-0.015, 0.0), (-0.04, -0.032), (0.06, 0.0)]
HUMAN = [(0.0, 0.05), (0.0, 0.02), (-0.018, 0.0), (0.0, 0.02), (0.018, 0.0)]

# A small alien lander: domed canopy, flared hull, two grasping legs beneath
# (it's a "carry the human away" ship, so the legs read as purposeful rather
# than decorative). One closed stroke, ten points.
LANDER_SHAPE = [
    (0.0, 0.046), (0.030, 0.020), (0.044, -0.008),
    (0.020, -0.030), (0.009, -0.010), (-0.009, -0.010),
    (-0.020, -0.030), (-0.044, -0.008), (-0.030, 0.020), (0.0, 0.046),
]


def _mirror(shape, flip):
    if flip >= 0:
        return shape
    return [(-x, y) for (x, y) in shape]


def _burst(cx, cy, r, n=7):
    return [(cx + math.cos(a) * r, cy + math.sin(a) * r)
            for a in [k / n * math.tau for k in range(n + 1)]]


def scene(world: DefenderWorld, cfg: Settings, t: float) -> Scene:
    out: Scene = []
    cground = cfg.beam(cfg.col_debris)
    cship = cfg.beam(cfg.col_ship)
    chuman = cfg.beam(cfg.col_bullet)
    clander = cfg.beam(cfg.col_saucer)
    cbullet = cfg.beam(cfg.col_bullet)
    ctext = cfg.beam(cfg.col_text)

    if world.state == DState.PLAY:
        px = world.player_x

        # terrain: real angular vertices only within the visible window,
        # converted to screen-relative x -- straight mountain edges, cheap
        prof = W.terrain_profile(px)
        pts = [(wx - px, W.GROUND_Y + h) for (wx, h) in prof]
        out.append((pts, cground))

        # humans on the ground (culled to the visible window)
        for h in world.humans:
            if not h.alive:
                continue
            d = W.wrap_delta(h.x, px)
            if abs(d) > W.VIEW_HALF:
                continue
            gy = W.GROUND_Y + W.terrain_h(h.x)
            out.append(([(d + x, gy + y) for (x, y) in HUMAN], chuman))

        # landers
        for L in world.landers:
            d = W.wrap_delta(L.x, px)
            if abs(d) > W.VIEW_HALF:
                continue
            out.append(([(d + x, L.y + y) for (x, y) in LANDER_SHAPE], clander))

        # bullets
        for b in world.bullets:
            d = W.wrap_delta(b.x, px)
            if abs(d) > W.VIEW_HALF:
                continue
            out.append(([(d - 0.03 * b.dir, b.y), (d + 0.03 * b.dir, b.y)], cbullet))
        for b in world.enemy_bullets:
            d = W.wrap_delta(b.x, px)
            if abs(d) > W.VIEW_HALF:
                continue
            out.append(([(d - 0.025 * b.dir, b.y), (d + 0.025 * b.dir, b.y)], clander))

        # explosions
        for ex in world.explosions:
            r = ex.radius()
            if r > 0.006:
                d = W.wrap_delta(ex.x, px)
                out.append((_burst(d, ex.y, r), clander))

        # player ship (blinks while invulnerable)
        blink_hidden = world.invuln > 0 and int(t * 10) % 2 == 1
        if not blink_hidden:
            ship = _mirror(SHIP_R, world.facing)
            out.append(([(x, world.player_y + y) for (x, y) in ship], cship))

    _hud(world, cfg, out, t, ctext, clander, cship)
    return out


def _hud(world, cfg, out, t, ctext, caccent, cship):
    for pl in font.text_polylines(str(world.score), -0.95, 0.86, 0.06):
        out.append((pl, ctext))
    for pl in font.text_polylines("WAVE %d" % world.wave, 0.0, 0.86, 0.055, center=True):
        out.append((pl, ctext))
    for i in range(min(world.lives, 5)):
        ix = 0.98 - i * 0.06
        out.append(([(ix - 0.02, 0.895), (ix, 0.915), (ix + 0.02, 0.895)], cship))

    if world.state == DState.READY:
        for pl in font.text_polylines("DEFENDER", 0.0, 0.50, 0.13, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("FIRE TO START", 0.0, -0.15, 0.075, center=True):
                out.append((pl, ctext))
        for pl in font.text_polylines("ARROWS FLY  SPACE FIRE", 0.0, -0.4, 0.045, center=True):
            out.append((pl, ctext))
    elif world.state == DState.BREAK:
        for pl in font.text_polylines("WAVE %d" % world.wave, 0.0, 0.05, 0.16, center=True):
            out.append((pl, caccent))
    elif world.state == DState.DEAD:
        for pl in font.text_polylines("SHIP LOST", 0.0, 0.30, 0.12, center=True):
            out.append((pl, caccent))
        for pl in font.text_polylines("SCORE " + str(world.score), 0.0, 0.06, 0.08, center=True):
            out.append((pl, ctext))
        for pl in font.text_polylines("WAVE " + str(world.wave), 0.0, -0.08, 0.06, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("FIRE TO RETRY", 0.0, -0.32, 0.07, center=True):
                out.append((pl, ctext))
