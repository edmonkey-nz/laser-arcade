"""MISSILE COMMAND -- pure logic, no pygame/laser.

Defend a row of cities from incoming missiles by clicking to detonate counter-
missiles in their path. Waves get busier (more missiles, faster) as you clear
them. Lose when every city is destroyed.

Kept deliberately sparse for the laser: only a handful of missiles and
explosions are ever on screen at once, so a frame never needs many shapes.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

GROUND_Y = -0.82
CITY_XS = [-0.72, -0.40, 0.0, 0.40, 0.72]
LAUNCH_POS = (0.0, GROUND_Y)

MAX_ENEMY_BASE = 3
MAX_ENEMY_CAP = 8          # concurrent missile cap grows with wave, up to this
MAX_PLAYER_CONCURRENT = 3
MAX_EXPLOSIONS = 3
FIRE_COOLDOWN = 0.28
PLAYER_SPEED = 2.1
EXPLODE_R = 0.14
EXPAND_T = 0.18
HOLD_T = 0.35
SHRINK_T = 0.22
EXPLOSION_LIFE = EXPAND_T + HOLD_T + SHRINK_T

BASE_ENEMY_SPEED = 0.36
BASE_MISSILES_PER_WAVE = 6
CITY_HIT_RADIUS = 0.14
WAVE_BREAK_TIME = 2.0       # pause between waves, showing "WAVE X"

SCORE_KILL = 25
SCORE_WAVE_CITY_BONUS = 100


class MState:
    READY = "ready"
    BREAK = "break"
    PLAY = "play"
    DEAD = "dead"


@dataclass
class MissileInput:
    aim: Optional[Tuple[float, float]] = None   # world position of the cursor
    fire: bool = False                           # edge: click to launch
    start: bool = False                          # edge: begin / restart


@dataclass
class EnemyMissile:
    x: float
    y: float
    tx: float
    ty: float
    speed: float
    ox: float = 0.0    # origin, for drawing the trail
    oy: float = 1.05

    def __post_init__(self):
        self.ox, self.oy = self.x, self.y


@dataclass
class PlayerMissile:
    x: float
    y: float
    tx: float
    ty: float
    ox: float = 0.0
    oy: float = 0.0

    def __post_init__(self):
        self.ox, self.oy = self.x, self.y


@dataclass
class Explosion:
    x: float
    y: float
    t: float = 0.0

    def radius(self) -> float:
        t = self.t
        if t < EXPAND_T:
            return EXPLODE_R * (t / EXPAND_T)
        if t < EXPAND_T + HOLD_T:
            return EXPLODE_R
        if t < EXPLOSION_LIFE:
            return EXPLODE_R * (1.0 - (t - EXPAND_T - HOLD_T) / SHRINK_T)
        return 0.0

    @property
    def alive(self) -> bool:
        return self.t < EXPLOSION_LIFE


@dataclass
class MissileWorld:
    state: str = MState.READY
    score: int = 0
    wave: int = 1
    cities: List[bool] = field(default_factory=lambda: [True] * len(CITY_XS))
    enemies: List[EnemyMissile] = field(default_factory=list)
    players: List[PlayerMissile] = field(default_factory=list)
    explosions: List[Explosion] = field(default_factory=list)
    cursor: Tuple[float, float] = (0.0, 0.0)
    events: List[str] = field(default_factory=list)
    break_timer: float = 0.0

    _spawned: int = 0
    _wave_total: int = 0
    _spawn_timer: float = 0.0
    _cooldown: float = 0.0

    def __post_init__(self):
        self._start_wave()

    # -- setup ----------------------------------------------------------
    def _reset_game(self) -> None:
        self.score = 0
        self.wave = 1
        self.cities = [True] * len(CITY_XS)
        self.enemies.clear()
        self.players.clear()
        self.explosions.clear()
        self._start_wave()
        self.state = MState.READY

    def _start_wave(self) -> None:
        self._wave_total = BASE_MISSILES_PER_WAVE + 3 * (self.wave - 1)
        self._spawned = 0
        self._spawn_timer = 0.5

    def _max_concurrent(self) -> int:
        # more missiles allowed in the air at once as waves climb, so a busier
        # wave actually feels busier instead of just trickling in slower
        return min(MAX_ENEMY_CAP, MAX_ENEMY_BASE + (self.wave - 1))

    def _enemy_speed(self) -> float:
        return BASE_ENEMY_SPEED * (1.0 + 0.16 * (self.wave - 1))

    def _spawn_interval(self) -> float:
        return max(0.30, 1.4 - 0.14 * (self.wave - 1))

    def _add_explosion(self, x: float, y: float) -> None:
        if len(self.explosions) >= MAX_EXPLOSIONS:
            self.explosions.pop(0)
        self.explosions.append(Explosion(x, y))

    # -- step -------------------------------------------------------------
    def update(self, dt: float, inp: MissileInput) -> None:
        self.events = []
        if inp.aim is not None:
            self.cursor = inp.aim

        if self.state == MState.READY:
            if inp.start:
                self.state = MState.BREAK
                self.break_timer = WAVE_BREAK_TIME
            return
        if self.state == MState.BREAK:
            self.break_timer -= dt
            if self.break_timer <= 0.0:
                self.state = MState.PLAY
            return
        if self.state == MState.DEAD:
            if inp.start:
                self._reset_game()
            return

        # ---- PLAY ----
        self._cooldown = max(0.0, self._cooldown - dt)
        if inp.fire and self._cooldown <= 0.0 and len(self.players) < MAX_PLAYER_CONCURRENT:
            tx, ty = inp.aim if inp.aim is not None else self.cursor
            ty = max(GROUND_Y, min(1.05, ty))
            self.players.append(PlayerMissile(LAUNCH_POS[0], LAUNCH_POS[1], tx, ty))
            self._cooldown = FIRE_COOLDOWN
            self.events.append("launch")

        # spawn enemies
        if self._spawned < self._wave_total:
            self._spawn_timer -= dt
            if self._spawn_timer <= 0.0 and len(self.enemies) < self._max_concurrent():
                self._spawn_enemy()
                self._spawned += 1
                self._spawn_timer = self._spawn_interval()

        # advance player missiles; explode on arrival
        for m in list(self.players):
            dx, dy = m.tx - m.x, m.ty - m.y
            dist = math.hypot(dx, dy)
            step = PLAYER_SPEED * dt
            if dist <= step:
                self._add_explosion(m.tx, m.ty)
                self.players.remove(m)
                self.events.append("burst")
            else:
                m.x += dx / dist * step
                m.y += dy / dist * step

        # advance enemy missiles; ground / city impact
        for m in list(self.enemies):
            dx, dy = m.tx - m.x, m.ty - m.y
            dist = math.hypot(dx, dy)
            step = m.speed * dt
            if dist <= step:
                self._impact(m)
                self.enemies.remove(m)
            else:
                m.x += dx / dist * step
                m.y += dy / dist * step

        # explosions age; destroy any enemy missile they catch
        for ex in list(self.explosions):
            ex.t += dt
            if not ex.alive:
                self.explosions.remove(ex)
                continue
            r = ex.radius()
            for m in list(self.enemies):
                if math.hypot(m.x - ex.x, m.y - ex.y) <= r:
                    self.enemies.remove(m)
                    self.score += SCORE_KILL
                    self.events.append("kill")

        # wave clear
        if (self._spawned >= self._wave_total and not self.enemies
                and not self.players and self.state == MState.PLAY):
            self.score += SCORE_WAVE_CITY_BONUS * sum(self.cities)
            self.wave += 1
            self._start_wave()
            self.state = MState.BREAK
            self.break_timer = WAVE_BREAK_TIME
            self.events.append("wave")

        if not any(self.cities):
            self.state = MState.DEAD
            self.events.append("gameover")

    def _spawn_enemy(self) -> None:
        alive_xs = [CITY_XS[i] for i, a in enumerate(self.cities) if a]
        tx = random.choice(alive_xs) if alive_xs else random.uniform(-0.8, 0.8)
        tx += random.uniform(-0.04, 0.04)
        sx = random.uniform(-1.0, 1.0)
        self.enemies.append(EnemyMissile(sx, 1.05, tx, GROUND_Y, self._enemy_speed()))

    def _impact(self, m: EnemyMissile) -> None:
        self._add_explosion(m.tx, m.ty)
        for i, x in enumerate(CITY_XS):
            if self.cities[i] and abs(x - m.tx) <= CITY_HIT_RADIUS:
                self.cities[i] = False
                self.events.append("city_lost")
                return
        self.events.append("ground")

    def cities_left(self) -> int:
        return sum(self.cities)
