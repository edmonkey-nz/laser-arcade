"""Two-player Pong simulation. Pure logic -- no pygame, no laser. World space is
the [-1, 1] square; the ball bounces between two vertical paddles.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List

# --- geometry / tuning (world units) ---------------------------------------
PADDLE_X = 0.90            # paddle centre distance from middle
PADDLE_HALF = 0.16        # half height of a paddle
PADDLE_W = 0.02           # half width of a paddle
PADDLE_SPEED = 1.7        # world units / second
PADDLE_Y_LIMIT = 0.80     # how far a paddle centre may travel from middle

BALL_R = 0.022
BALL_SPEED0 = 1.05
BALL_SPEED_MAX = 2.3
SPEED_GAIN = 1.06         # per paddle hit
MAX_BOUNCE = 0.9          # radians of vertical deflection at a paddle edge
WALL_Y = 0.90             # ball reflects off top/bottom here

SERVE_DELAY = 0.9         # seconds the ball hangs at centre before launching
WIN_SCORE = 7


class PState:
    SERVE = "serve"
    PLAY = "play"
    OVER = "over"


@dataclass
class PongInput:
    p1_dir: int = 0          # -1 down, +1 up
    p2_dir: int = 0
    serve: bool = False      # edge: launch now / restart match


@dataclass
class PongWorld:
    p1_y: float = 0.0
    p2_y: float = 0.0
    ball_x: float = 0.0
    ball_y: float = 0.0
    ball_vx: float = 0.0
    ball_vy: float = 0.0
    speed: float = BALL_SPEED0
    score: List[int] = field(default_factory=lambda: [0, 0])
    state: str = PState.SERVE
    serve_timer: float = SERVE_DELAY
    serve_dir: int = -1      # which way the next serve travels
    winner: int = 0          # 1 or 2 when state == OVER
    events: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.reset_match()

    # -- setup --------------------------------------------------------------
    def reset_match(self) -> None:
        self.score = [0, 0]
        self.p1_y = self.p2_y = 0.0
        self.winner = 0
        self._new_serve(random.choice((-1, 1)))

    def _new_serve(self, direction: int) -> None:
        self.ball_x = self.ball_y = 0.0
        self.ball_vx = self.ball_vy = 0.0
        self.speed = BALL_SPEED0
        self.serve_dir = direction
        self.serve_timer = SERVE_DELAY
        self.state = PState.SERVE

    def _launch(self) -> None:
        angle = random.uniform(-0.35, 0.35)   # small vertical spread
        import math
        self.ball_vx = self.serve_dir * self.speed * math.cos(angle)
        self.ball_vy = self.speed * math.sin(angle)
        self.state = PState.PLAY
        self.events.append("serve")

    # -- step ---------------------------------------------------------------
    def update(self, dt: float, inp: PongInput) -> None:
        self.events = []

        # paddles always respond (feels better; lets players pre-position)
        self.p1_y = _clamp(self.p1_y + inp.p1_dir * PADDLE_SPEED * dt,
                           -PADDLE_Y_LIMIT, PADDLE_Y_LIMIT)
        self.p2_y = _clamp(self.p2_y + inp.p2_dir * PADDLE_SPEED * dt,
                           -PADDLE_Y_LIMIT, PADDLE_Y_LIMIT)

        if self.state == PState.SERVE:
            self.serve_timer -= dt
            if inp.serve or self.serve_timer <= 0.0:
                self._launch()
            return
        if self.state == PState.OVER:
            if inp.serve:
                self.reset_match()
            return

        # PLAY
        prev_x = self.ball_x
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt

        # top / bottom walls
        if self.ball_y + BALL_R > WALL_Y and self.ball_vy > 0:
            self.ball_y = WALL_Y - BALL_R
            self.ball_vy = -self.ball_vy
            self.events.append("wall")
        elif self.ball_y - BALL_R < -WALL_Y and self.ball_vy < 0:
            self.ball_y = -WALL_Y + BALL_R
            self.ball_vy = -self.ball_vy
            self.events.append("wall")

        # paddles
        self._paddle_bounce(left=True, prev_x=prev_x)
        self._paddle_bounce(left=False, prev_x=prev_x)

        # scoring
        if self.ball_x < -1.05:
            self._point(scorer=2)
        elif self.ball_x > 1.05:
            self._point(scorer=1)

    def _paddle_bounce(self, left: bool, prev_x: float) -> None:
        import math
        # Bounce only when the ball's leading edge *crosses* the paddle's inner
        # face this frame (tunnel-proof, and won't fire for a ball already past
        # the paddle heading for the goal).
        if left:
            if self.ball_vx >= 0:
                return
            face = -PADDLE_X + PADDLE_W
            prev_edge = prev_x - BALL_R
            cur_edge = self.ball_x - BALL_R
            if not (prev_edge > face and cur_edge <= face):
                return
            py = self.p1_y
            direction = 1
        else:
            if self.ball_vx <= 0:
                return
            face = PADDLE_X - PADDLE_W
            prev_edge = prev_x + BALL_R
            cur_edge = self.ball_x + BALL_R
            if not (prev_edge < face and cur_edge >= face):
                return
            py = self.p2_y
            direction = -1

        if abs(self.ball_y - py) > PADDLE_HALF + BALL_R:
            return  # missed

        # reflect, deflect by where it struck the paddle, and speed up
        self.speed = min(self.speed * SPEED_GAIN, BALL_SPEED_MAX)
        offset = _clamp((self.ball_y - py) / PADDLE_HALF, -1.0, 1.0)
        angle = offset * MAX_BOUNCE
        self.ball_vx = direction * self.speed * math.cos(angle)
        self.ball_vy = self.speed * math.sin(angle)
        # push the ball off the paddle face so it can't re-trigger next frame
        self.ball_x = face + direction * (BALL_R + 0.001)
        self.events.append("paddle")

    def _point(self, scorer: int) -> None:
        self.score[scorer - 1] += 1
        self.events.append("score")
        if self.score[scorer - 1] >= WIN_SCORE:
            self.winner = scorer
            self.state = PState.OVER
            self.events.append("win")
        else:
            # serve toward the player who was just scored on (they receive)
            self._new_serve(direction=(-1 if scorer == 2 else 1))


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
