"""Turn the Pong world into a scene (world-space polylines + colour)."""
from __future__ import annotations

from engine import font
from engine.config import Settings
from engine.game import Scene

from . import world as W
from .world import PongWorld, PState


def _rect(cx: float, cy: float, hw: float, hh: float):
    """Closed rectangle outline centred on (cx, cy)."""
    return [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh),
            (cx - hw, cy + hh), (cx - hw, cy - hh)]


def scene(world: PongWorld, cfg: Settings, t: float) -> Scene:
    out: Scene = []
    cmain = cfg.beam(cfg.col_ship)      # paddles + ball (cyan)
    ct = cfg.beam(cfg.col_text)
    cnet = cfg.beam(cfg.col_debris)     # dim amber net

    # centre net: a few short dashes (kept sparse for the point budget)
    dashes = 6
    for i in range(dashes):
        y0 = -0.82 + i * (1.64 / dashes)
        out.append(([(0.0, y0), (0.0, y0 + 0.09)], cnet))

    # paddles
    out.append((_rect(-W.PADDLE_X, world.p1_y, W.PADDLE_W, W.PADDLE_HALF), cmain))
    out.append((_rect(W.PADDLE_X, world.p2_y, W.PADDLE_W, W.PADDLE_HALF), cmain))

    # ball (a small square; on serve it sits centred and blinks)
    show_ball = world.state != PState.SERVE or int(t * 4) % 2 == 0
    if show_ball:
        out.append((_rect(world.ball_x, world.ball_y, W.BALL_R, W.BALL_R), cmain))

    # scores, up top either side of the net
    for pl in font.text_polylines(str(world.score[0]), -0.33, 0.72, 0.16, center=True):
        out.append((pl, ct))
    for pl in font.text_polylines(str(world.score[1]), 0.33, 0.72, 0.16, center=True):
        out.append((pl, ct))

    # messages
    if world.state == PState.OVER:
        who = "PLAYER 1 WINS" if world.winner == 1 else "PLAYER 2 WINS"
        for pl in font.text_polylines(who, 0.0, 0.02, 0.12, center=True):
            out.append((pl, ct))
        if int(t * 2) % 2 == 0:
            for pl in font.text_polylines("PRESS ENTER", 0.0, -0.28, 0.08, center=True):
                out.append((pl, ct))
    elif world.state == PState.SERVE and int(t * 2) % 2 == 0:
        for pl in font.text_polylines("READY", 0.0, -0.55, 0.07, center=True):
            out.append((pl, ct))

    return out
