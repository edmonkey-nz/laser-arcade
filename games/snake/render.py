"""Turn the Snake world into a scene. The whole body is one connected
polyline through grid-cell centres -- as cheap as it gets: exactly as many
points as there are segments, no per-segment shape overhead.
"""
from __future__ import annotations

from engine import font
from engine.config import Settings
from engine.game import Scene

from . import world as W
from .world import SnakeWorld, SState

FOOD = [(0.0, 1.0), (0.7, 0.0), (0.0, -1.0), (-0.7, 0.0), (0.0, 1.0)]


def scene(world: SnakeWorld, cfg: Settings, t: float) -> Scene:
    out: Scene = []
    cborder = cfg.beam(cfg.col_debris)
    csnake = cfg.beam(cfg.col_ship)
    cfood = cfg.beam(cfg.col_bullet)
    ctext = cfg.beam(cfg.col_text)

    b = W.BOARD_MIN, W.BOARD_MAX
    out.append(([(b[0], b[0]), (b[1], b[0]), (b[1], b[1]), (b[0], b[1]), (b[0], b[0])], cborder))

    if world.state != SState.DEAD:
        snake_pts = [W.grid_to_world(x, y) for (x, y) in world.body]
        out.append((snake_pts, csnake))
        # a small tick marking the head, for direction clarity
        hx, hy = snake_pts[0]
        out.append(([(hx - W.CELL * 0.15, hy), (hx + W.CELL * 0.15, hy)], ctext))

        fx, fy = W.grid_to_world(*world.food)
        r = W.CELL * 0.35
        out.append(([(fx + px * r, fy + py * r) for (px, py) in FOOD], cfood))

    _hud(world, cfg, out, t, ctext, cfood)
    return out


def _hud(world, cfg, out, t, ctext, caccent):
    for pl in font.text_polylines(str(world.score), -0.95, 0.86, 0.06):
        out.append((pl, ctext))
    if world.best > 0:
        text = "BEST " + str(world.best)
        w = font.text_width(text) * (0.05 / 6.0)
        for pl in font.text_polylines(text, 0.95 - w, 0.87, 0.05):
            out.append((pl, ctext))

    if world.state == SState.READY:
        for pl in font.text_polylines("SNAKE", 0.0, 0.45, 0.14, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("ENTER TO START", 0.0, -0.5, 0.075, center=True):
                out.append((pl, ctext))
    elif world.state == SState.DEAD:
        for pl in font.text_polylines("CRASHED", 0.0, 0.20, 0.13, center=True):
            out.append((pl, caccent))
        for pl in font.text_polylines("SCORE " + str(world.score), 0.0, -0.02, 0.08, center=True):
            out.append((pl, ctext))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("ENTER TO RETRY", 0.0, -0.24, 0.07, center=True):
                out.append((pl, ctext))
