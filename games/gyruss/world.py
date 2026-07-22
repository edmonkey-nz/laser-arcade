"""GYRUSS -- a radial tunnel shooter, as pure logic.

The player orbits a ring near the screen edge and fires inward, toward the
centre. Enemies spawn at the centre and spiral outward to a formation ring;
from there they peel off and dive outward at the player, homing a little as
they come. Waves get busier the longer you survive; lose all your lives and
you're out.

Kept deliberately sparse for the laser: hard caps on concurrent enemies,
bullets and explosions, so a frame never needs many shapes.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional

R_PLAYER = 0.82        # player's orbit radius
R_FORM = 0.52           # formation hold radius, before enemies dive
R_SPAWN = 0.05          # enemies spawn near the centre

ORBIT_SPEED = 2.6       # player angular speed, rad/s at full stick
INVULN_TIME = 1.4       # seconds of blink-invulnerability after being hit

BULLET_SPEED = 1.7      # radius units/sec, travelling inward
BULLET_COOLDOWN = 0.20
MAX_BULLETS = 3

MAX_ENEMIES = 6
BASE_OUT_SPEED = 0.30    # radial speed spiralling out to the formation ring
BASE_DIVE_SPEED = 0.55   # radial speed while diving at the player
SPIRAL_K = 1.1           # angular-speed gain while spiralling out (tighter near centre)
DIVE_CHANCE = 0.35       # chance/sec a formation enemy peels off to dive
DIVE_HOMING = 1.6        # how hard a diving enemy steers toward the player's angle

HIT_R = 0.055            # bullet/enemy radius-match tolerance
HIT_ANGLE = 0.17          # bullet/enemy angle-match tolerance (radians)
PLAYER_HIT_MARGIN = 0.07  # how close to R_PLAYER counts as "reached the player"
PLAYER_HIT_ANGLE = 0.20

MAX_EXPLOSIONS = 2
EXPLOSION_LIFE = 0.35

LIVES_START = 3
SCORE_KILL = 15
SCORE_WAVE_BONUS = 50


class GState:
    READY = "ready"
    PLAY = "play"
    DEAD = "dead"


def _wrap(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    return (a + math.pi) % math.tau - math.pi


@dataclass
class GyrussInput:
    steer: float = 0.0     # -1 left .. +1 right
    fire: bool = False      # held: keep firing (subject to cooldown)
    start: bool = False     # edge: begin / restart


@dataclass
class Enemy:
    angle: float
    r: float
    dir: float              # +1 / -1 spin direction
    speed_mul: float = 1.0
    diving: bool = False


@dataclass
class Bullet:
    angle: float
    r: float


@dataclass
class Explosion:
    x: float
    y: float
    t: float = 0.0

    @property
    def alive(self) -> bool:
        return self.t < EXPLOSION_LIFE

    def radius(self) -> float:
        f = self.t / EXPLOSION_LIFE
        return 0.10 * math.sin(math.pi * min(1.0, max(0.0, f)))


@dataclass
class GyrussWorld:
    state: str = GState.READY
    score: int = 0
    wave: int = 1
    lives: int = LIVES_START
    player_angle: float = -math.pi / 2   # start at the bottom of the ring
    invuln: float = 0.0
    enemies: List[Enemy] = field(default_factory=list)
    bullets: List[Bullet] = field(default_factory=list)
    explosions: List[Explosion] = field(default_factory=list)
    events: List[str] = field(default_factory=list)

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
        self.lives = LIVES_START
        self.player_angle = -math.pi / 2
        self.invuln = 0.0
        self.enemies.clear()
        self.bullets.clear()
        self.explosions.clear()
        self._start_wave()
        self.state = GState.READY

    def _start_wave(self) -> None:
        self._wave_total = 6 + 2 * (self.wave - 1)
        self._spawned = 0
        self._spawn_timer = 0.5

    def _spawn_interval(self) -> float:
        return max(0.35, 1.1 - 0.06 * (self.wave - 1))

    def _speed_mul(self) -> float:
        return 1.0 + 0.10 * (self.wave - 1)

    def _add_explosion(self, x: float, y: float) -> None:
        if len(self.explosions) >= MAX_EXPLOSIONS:
            self.explosions.pop(0)
        self.explosions.append(Explosion(x, y))

    # -- step -------------------------------------------------------------
    def update(self, dt: float, inp: GyrussInput) -> None:
        self.events = []
        if self.state == GState.READY:
            if inp.start:
                self.state = GState.PLAY
            return
        if self.state == GState.DEAD:
            if inp.start:
                self._reset_game()
            return

        # ---- PLAY ----
        self.player_angle += inp.steer * ORBIT_SPEED * dt
        self.invuln = max(0.0, self.invuln - dt)

        self._cooldown = max(0.0, self._cooldown - dt)
        if inp.fire and self._cooldown <= 0.0 and len(self.bullets) < MAX_BULLETS:
            self.bullets.append(Bullet(self.player_angle, R_PLAYER))
            self._cooldown = BULLET_COOLDOWN
            self.events.append("fire")

        # spawn enemies
        if self._spawned < self._wave_total:
            self._spawn_timer -= dt
            if self._spawn_timer <= 0.0 and len(self.enemies) < MAX_ENEMIES:
                self._spawn_enemy()
                self._spawned += 1
                self._spawn_timer = self._spawn_interval()

        # advance bullets (inward); drop once past the centre
        for b in list(self.bullets):
            b.r -= BULLET_SPEED * dt
            if b.r <= 0.0:
                self.bullets.remove(b)

        # advance enemies
        mul = self._speed_mul()
        for e in list(self.enemies):
            if not e.diving:
                # spiralling out to the formation ring: angular speed eases as
                # the radius grows, giving the classic tightening spiral
                ang_speed = SPIRAL_K / (e.r + 0.18) * e.dir
                e.angle += ang_speed * dt
                e.r += BASE_OUT_SPEED * mul * e.speed_mul * dt
                if e.r >= R_FORM:
                    e.r = R_FORM
                    if random.random() < DIVE_CHANCE * mul * dt:
                        e.diving = True
            else:
                diff = _wrap(self.player_angle - e.angle)
                e.angle += max(-3.0, min(3.0, diff * DIVE_HOMING)) * dt
                e.r += BASE_DIVE_SPEED * mul * e.speed_mul * dt

            if e.r >= R_PLAYER - PLAYER_HIT_MARGIN:
                diff = abs(_wrap(self.player_angle - e.angle))
                if diff < PLAYER_HIT_ANGLE and self.invuln <= 0.0:
                    self._hit_player(e)
                elif e.r >= R_PLAYER + 0.05:
                    self.enemies.remove(e)   # dived past without hitting

        # bullet/enemy collisions
        for b in list(self.bullets):
            for e in list(self.enemies):
                if abs(b.r - e.r) < HIT_R and abs(_wrap(b.angle - e.angle)) < HIT_ANGLE:
                    self._add_explosion(e.r * math.cos(e.angle), e.r * math.sin(e.angle))
                    self.enemies.remove(e)
                    if b in self.bullets:
                        self.bullets.remove(b)
                    self.score += SCORE_KILL
                    self.events.append("kill")
                    break

        for ex in list(self.explosions):
            ex.t += dt
            if not ex.alive:
                self.explosions.remove(ex)

        # wave clear
        if (self._spawned >= self._wave_total and not self.enemies
                and self.state == GState.PLAY):
            self.score += SCORE_WAVE_BONUS * self.wave
            self.wave += 1
            self._start_wave()
            self.events.append("wave")

    def _spawn_enemy(self) -> None:
        angle = random.uniform(-math.pi, math.pi)
        d = random.choice((-1.0, 1.0))
        self.enemies.append(Enemy(angle, R_SPAWN, d, self._speed_mul()))

    def _hit_player(self, e: Enemy) -> None:
        if e in self.enemies:
            self._add_explosion(R_PLAYER * math.cos(self.player_angle),
                                R_PLAYER * math.sin(self.player_angle))
            self.enemies.remove(e)
        self.lives -= 1
        self.invuln = INVULN_TIME
        self.events.append("hit")
        if self.lives <= 0:
            self.lives = 0
            self.state = GState.DEAD
            self.events.append("gameover")
