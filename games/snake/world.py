"""SNAKE -- the classic grid game, as pure logic.

Moves on a fixed tick (not continuously): a direction queued this frame takes
effect on the next grid step, and can't be the exact reverse of the current
heading (the classic can't-turn-into-yourself rule). Eating food grows the
snake by one segment and speeds the tick up slightly, capped at a maximum.

Trivially cheap for the laser: the whole snake is one connected polyline
through grid-cell centres (just as many points as there are segments), plus a
border and a food marker.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

GRID_N = 16
BOARD_MIN = -0.80
BOARD_MAX = 0.80
CELL = (BOARD_MAX - BOARD_MIN) / GRID_N

START_LEN = 3
TICK_START = 0.16
TICK_MIN = 0.075
TICK_STEP = 0.004        # tick shortens by this much per food, down to TICK_MIN

SCORE_FOOD = 10

DIRS = {
    "up": (0, 1),
    "down": (0, -1),
    "left": (-1, 0),
    "right": (1, 0),
}


class SState:
    READY = "ready"
    PLAY = "play"
    DEAD = "dead"


@dataclass
class SnakeInput:
    dir: Optional[str] = None    # "up"/"down"/"left"/"right", or None
    start: bool = False


def grid_to_world(gx: int, gy: int) -> Tuple[float, float]:
    return (BOARD_MIN + (gx + 0.5) * CELL, BOARD_MIN + (gy + 0.5) * CELL)


@dataclass
class SnakeWorld:
    state: str = SState.READY
    score: int = 0
    best: int = 0
    body: List[Tuple[int, int]] = field(default_factory=list)  # head first
    direction: Tuple[int, int] = (1, 0)
    pending_dir: Tuple[int, int] = (1, 0)
    food: Tuple[int, int] = (0, 0)
    tick: float = TICK_START
    _timer: float = 0.0
    events: List[str] = field(default_factory=list)

    def __post_init__(self):
        self._new_game()

    def _new_game(self) -> None:
        cx, cy = GRID_N // 2, GRID_N // 2
        self.body = [(cx - i, cy) for i in range(START_LEN)]
        self.direction = (1, 0)
        self.pending_dir = (1, 0)
        self.tick = TICK_START
        self._timer = self.tick
        self.score = 0
        self._place_food()
        self.state = SState.READY

    def _place_food(self) -> None:
        occupied = set(self.body)
        free = [(x, y) for x in range(GRID_N) for y in range(GRID_N)
                if (x, y) not in occupied]
        self.food = random.choice(free) if free else self.body[0]

    def update(self, dt: float, inp: SnakeInput) -> None:
        self.events = []
        if self.state == SState.READY:
            if inp.dir:
                self._queue_dir(inp.dir)
            if inp.start:
                self.state = SState.PLAY
            return
        if self.state == SState.DEAD:
            if inp.start:
                self._new_game()
            return

        # PLAY
        if inp.dir:
            self._queue_dir(inp.dir)

        self._timer -= dt
        if self._timer > 0.0:
            return
        self._timer += self.tick
        self._step()

    def _queue_dir(self, name: str) -> None:
        dx, dy = DIRS[name]
        cx, cy = self.direction
        if (dx, dy) == (-cx, -cy):
            return  # can't reverse directly into yourself
        self.pending_dir = (dx, dy)

    def _step(self) -> None:
        self.direction = self.pending_dir
        hx, hy = self.body[0]
        dx, dy = self.direction
        nx, ny = hx + dx, hy + dy

        if not (0 <= nx < GRID_N and 0 <= ny < GRID_N):
            self._die()
            return
        # allow moving into the current tail cell (it vacates this step,
        # unless we're about to grow -- then it doesn't, and that's a crash)
        grows = (nx, ny) == self.food
        body_check = self.body if grows else self.body[:-1]
        if (nx, ny) in body_check:
            self._die()
            return

        self.body.insert(0, (nx, ny))
        if grows:
            self.score += SCORE_FOOD
            self.tick = max(TICK_MIN, self.tick - TICK_STEP)
            self._place_food()
            self.events.append("eat")
        else:
            self.body.pop()

    def _die(self) -> None:
        self.state = SState.DEAD
        self.best = max(self.best, self.score)
        self.events.append("crash")
