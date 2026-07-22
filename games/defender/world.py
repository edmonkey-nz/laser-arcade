"""DEFENDER -- a side-scrolling rescue shooter, as pure logic.

The world is a wraparound strip (a cylinder in x) much wider than the screen;
the camera follows the ship. Humans stand on the terrain; Landers descend to
grab one and carry it upward -- shoot a Lander to kill it (freeing any human
it's carrying), or let it escape off the top and the human is lost for good.
Once no humans remain, new Landers spawn as fast, direct chasers instead.

Kept sparse for the laser two ways: only entities within the visible window
are ever turned into shapes, and concurrent enemies/bullets/explosions are
hard-capped, so a frame never needs many shapes regardless of world size.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional

WORLD_WIDTH = 6.0
VIEW_HALF = 1.05            # a bit past the screen edge, for clean culling
GROUND_Y = -0.82

PLAYER_Y_MIN = GROUND_Y + 0.14
PLAYER_Y_MAX = 0.88
PLAYER_MAX_VX = 1.15          # top horizontal speed
PLAYER_ACCEL_X = 2.6           # thrust: how fast vx ramps toward max
PLAYER_DRAG_X = 1.7            # coasting deceleration when no horizontal input
PLAYER_SPEED_Y = 1.05           # vertical stays direct/responsive, as in the original
INVULN_TIME = 1.2

BULLET_SPEED = 2.1
FIRE_COOLDOWN = 0.16
MAX_PLAYER_BULLETS = 4

MAX_ENEMY_BASE = 2
MAX_ENEMY_CAP = 7
BASE_LANDERS_PER_WAVE = 5
SEEK_SPEED = 0.34
CARRY_SPEED = 0.30
CHASE_SPEED = 0.55
CAPTURE_R = 0.07
ESCAPE_Y = 0.92
COLLIDE_R = 0.075
MAX_ENEMY_BULLETS = 3
ENEMY_BULLET_SPEED = 0.9
ENEMY_FIRE_COOLDOWN = 1.4
ENEMY_FIRE_RANGE = 0.9

MAX_EXPLOSIONS = 2
EXPLOSION_LIFE = 0.35

HUMANS_START = 6
LIVES_START = 3
WAVE_BREAK_TIME = 2.0

SCORE_KILL = 20
SCORE_RESCUE = 30
SCORE_WAVE_HUMAN_BONUS = 40


# Angular terrain profile: (world_x, height) vertices, x ascending in
# [0, WORLD_WIDTH). Straight edges between real vertices -- cheaper for a
# laser than a smoothly-sampled curve (a curve needs many points to avoid
# faceting; sharp vertices just need one point each, joined by clean straight
# strokes) and it reads as proper jagged Defender-style terrain. A tour of
# terrain types around the loop: twin peaks, a mesa, a canyon, rolling hills,
# a lone tall peak.
_TERRAIN_VERTS = [
    (0.00, 0.00), (0.35, 0.02), (0.70, 0.14), (0.95, 0.03), (1.15, 0.12),
    (1.45, 0.01), (1.90, 0.01), (2.20, 0.08), (2.55, 0.08), (2.85, 0.00),
    (3.10, -0.05), (3.35, -0.06), (3.60, -0.02), (3.90, 0.03), (4.15, 0.00),
    (4.40, 0.04), (4.65, 0.00), (4.90, 0.02), (5.20, 0.16), (5.45, 0.02),
    (5.75, 0.00),
]
_TERRAIN_EXT = _TERRAIN_VERTS + [(WORLD_WIDTH, _TERRAIN_VERTS[0][1])]


def terrain_h(x: float) -> float:
    """Terrain height at world x: piecewise-linear between the hand-authored
    vertices above, wrapping seamlessly at WORLD_WIDTH."""
    x = x % WORLD_WIDTH
    verts = _TERRAIN_EXT
    for i in range(len(verts) - 1):
        x0, h0 = verts[i]
        x1, h1 = verts[i + 1]
        if x0 <= x <= x1:
            f = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return h0 + (h1 - h0) * f
    return verts[-1][1]


def terrain_profile(center_x: float, half_width: float = VIEW_HALF):
    """(world_x, height) pairs spanning [center_x-half_width, center_x+half_width]
    at the real angular vertices only, plus interpolated endpoints -- so the
    rendered ground is genuinely angular, not a dense curve, and only costs a
    handful of points regardless of how wide the world is."""
    lo, hi = center_x - half_width, center_x + half_width
    k_lo = int(math.floor(lo / WORLD_WIDTH)) - 1
    k_hi = int(math.ceil(hi / WORLD_WIDTH)) + 1
    pts = []
    for k in range(k_lo, k_hi + 1):
        base = k * WORLD_WIDTH
        for vx, vh in _TERRAIN_VERTS:
            wx = base + vx
            if lo <= wx <= hi:
                pts.append((wx, vh))
    pts.sort()
    out = [(lo, terrain_h(lo))]
    for wx, vh in pts:
        if wx > out[-1][0] + 1e-9:
            out.append((wx, vh))
    if out[-1][0] < hi - 1e-9:
        out.append((hi, terrain_h(hi)))
    return out


def wrap_delta(a: float, b: float) -> float:
    """Shortest signed distance from b to a on the WORLD_WIDTH cylinder."""
    d = (a - b + WORLD_WIDTH / 2.0) % WORLD_WIDTH - WORLD_WIDTH / 2.0
    return d


def wrap_x(x: float) -> float:
    return x % WORLD_WIDTH


class DState:
    READY = "ready"
    BREAK = "break"
    PLAY = "play"
    DEAD = "dead"


@dataclass
class DefenderInput:
    steer_x: float = 0.0
    steer_y: float = 0.0
    fire: bool = False
    start: bool = False


@dataclass
class Human:
    x: float
    alive: bool = True
    claimed: bool = False


@dataclass
class Lander:
    x: float
    y: float
    state: str = "seek"          # seek | carry | chase
    target: Optional[int] = None  # index into world.humans
    speed_mul: float = 1.0
    fire_cd: float = 0.0


@dataclass
class Bullet:
    x: float
    y: float
    dir: float   # +1 / -1 (world-x direction)


@dataclass
class Explosion:
    x: float
    y: float
    t: float = 0.0

    @property
    def alive(self) -> bool:
        return self.t < EXPLOSION_LIFE

    def radius(self) -> float:
        f = min(1.0, max(0.0, self.t / EXPLOSION_LIFE))
        return 0.10 * math.sin(math.pi * f)


@dataclass
class DefenderWorld:
    state: str = DState.READY
    score: int = 0
    wave: int = 1
    lives: int = LIVES_START
    invuln: float = 0.0
    player_x: float = 0.0
    player_y: float = 0.0
    player_vx: float = 0.0
    facing: float = 1.0
    humans: List[Human] = field(default_factory=list)
    landers: List[Lander] = field(default_factory=list)
    bullets: List[Bullet] = field(default_factory=list)
    enemy_bullets: List[Bullet] = field(default_factory=list)
    explosions: List[Explosion] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    break_timer: float = 0.0

    _spawned: int = 0
    _wave_total: int = 0
    _spawn_timer: float = 0.0
    _cooldown: float = 0.0

    def __post_init__(self):
        self._place_humans()
        self._start_wave()

    # -- setup ----------------------------------------------------------
    def _place_humans(self) -> None:
        self.humans = [Human(x=(i + 0.5) * WORLD_WIDTH / HUMANS_START)
                       for i in range(HUMANS_START)]

    def _reset_game(self) -> None:
        self.score = 0
        self.wave = 1
        self.lives = LIVES_START
        self.invuln = 0.0
        self.player_x = 0.0
        self.player_y = 0.0
        self.player_vx = 0.0
        self.facing = 1.0
        self.landers.clear()
        self.bullets.clear()
        self.enemy_bullets.clear()
        self.explosions.clear()
        self._place_humans()
        self._start_wave()
        self.state = DState.READY

    def _start_wave(self) -> None:
        self._wave_total = BASE_LANDERS_PER_WAVE + 3 * (self.wave - 1)
        self._spawned = 0
        self._spawn_timer = 0.5

    def _max_concurrent(self) -> int:
        return min(MAX_ENEMY_CAP, MAX_ENEMY_BASE + (self.wave - 1))

    def _speed_mul(self) -> float:
        return 1.0 + 0.12 * (self.wave - 1)

    def _spawn_interval(self) -> float:
        return max(0.30, 1.3 - 0.12 * (self.wave - 1))

    def _humans_alive(self) -> int:
        return sum(1 for h in self.humans if h.alive)

    def _add_explosion(self, x: float, y: float) -> None:
        if len(self.explosions) >= MAX_EXPLOSIONS:
            self.explosions.pop(0)
        self.explosions.append(Explosion(x, y))

    # -- step -------------------------------------------------------------
    def update(self, dt: float, inp: DefenderInput) -> None:
        self.events = []
        if self.state == DState.READY:
            if inp.start:
                self.state = DState.BREAK
                self.break_timer = WAVE_BREAK_TIME
            return
        if self.state == DState.BREAK:
            self.break_timer -= dt
            if self.break_timer <= 0.0:
                self.state = DState.PLAY
            return
        if self.state == DState.DEAD:
            if inp.start:
                self._reset_game()
            return

        # ---- PLAY ----
        self.invuln = max(0.0, self.invuln - dt)
        if abs(inp.steer_x) > 0.05:
            self.facing = 1.0 if inp.steer_x > 0 else -1.0
            self.player_vx += inp.steer_x * PLAYER_ACCEL_X * dt
            self.player_vx = _clamp(self.player_vx, -PLAYER_MAX_VX, PLAYER_MAX_VX)
        else:
            # no thrust: coast and gradually slow, don't stop dead
            if self.player_vx > 0.0:
                self.player_vx = max(0.0, self.player_vx - PLAYER_DRAG_X * dt)
            elif self.player_vx < 0.0:
                self.player_vx = min(0.0, self.player_vx + PLAYER_DRAG_X * dt)
        self.player_x = wrap_x(self.player_x + self.player_vx * dt)
        self.player_y = _clamp(self.player_y + inp.steer_y * PLAYER_SPEED_Y * dt,
                               PLAYER_Y_MIN, PLAYER_Y_MAX)

        self._cooldown = max(0.0, self._cooldown - dt)
        if inp.fire and self._cooldown <= 0.0 and len(self.bullets) < MAX_PLAYER_BULLETS:
            self.bullets.append(Bullet(self.player_x, self.player_y, self.facing))
            self._cooldown = FIRE_COOLDOWN
            self.events.append("fire")

        self._spawn_step(dt)
        self._advance_bullets(dt)
        self._advance_landers(dt)
        self._collisions()
        self._age_explosions(dt)
        self._wave_check()

        if self.lives <= 0 and self.state == DState.PLAY:
            self.lives = 0
            self.state = DState.DEAD
            self.events.append("gameover")

    def _spawn_step(self, dt: float) -> None:
        if self._spawned >= self._wave_total:
            return
        self._spawn_timer -= dt
        if self._spawn_timer <= 0.0 and len(self.landers) < self._max_concurrent():
            self._spawn_lander()
            self._spawned += 1
            self._spawn_timer = self._spawn_interval()

    def _spawn_lander(self) -> None:
        x = wrap_x(self.player_x + random.uniform(-WORLD_WIDTH / 2, WORLD_WIDTH / 2))
        y = random.uniform(0.5, 0.9)
        idx = self._claim_target()
        state = "seek" if idx is not None else "chase"
        self.landers.append(Lander(x, y, state=state, target=idx,
                                   speed_mul=self._speed_mul()))

    def _claim_target(self) -> Optional[int]:
        candidates = [i for i, h in enumerate(self.humans) if h.alive and not h.claimed]
        if not candidates:
            return None
        i = random.choice(candidates)
        self.humans[i].claimed = True
        return i

    def _advance_bullets(self, dt: float) -> None:
        for b in list(self.bullets):
            b.x = wrap_x(b.x + b.dir * BULLET_SPEED * dt)
            if abs(wrap_delta(b.x, self.player_x)) > VIEW_HALF + 0.2:
                self.bullets.remove(b)
        for b in list(self.enemy_bullets):
            b.x = wrap_x(b.x + b.dir * ENEMY_BULLET_SPEED * dt)
            if abs(wrap_delta(b.x, self.player_x)) > VIEW_HALF + 0.2:
                self.enemy_bullets.remove(b)

    def _advance_landers(self, dt: float) -> None:
        mul = self._speed_mul()
        for L in list(self.landers):
            if L.state == "seek":
                if L.target is None or not self.humans[L.target].alive:
                    L.target = self._claim_target()
                    if L.target is None:
                        L.state = "chase"
                        continue
                h = self.humans[L.target]
                dx = wrap_delta(h.x, L.x)
                gy = GROUND_Y + terrain_h(h.x) + 0.08
                dy = gy - L.y
                dist = math.hypot(dx, dy)
                step = SEEK_SPEED * mul * dt
                if dist <= max(step, CAPTURE_R):
                    L.state = "carry"
                    h.alive = False
                    self.events.append("capture")
                else:
                    L.x = wrap_x(L.x + dx / dist * step)
                    L.y += dy / dist * step
            elif L.state == "carry":
                L.y += CARRY_SPEED * mul * dt
                if L.target is not None:
                    self.humans[L.target].x = L.x   # carried along horizontally
                if L.y >= ESCAPE_Y:
                    if L.target is not None:
                        self.humans[L.target].alive = False
                        self.humans[L.target].claimed = False
                    self.landers.remove(L)
                    self.events.append("human_lost")
                    continue
            else:  # chase
                dx = wrap_delta(self.player_x, L.x)
                dy = self.player_y - L.y
                dist = math.hypot(dx, dy) or 1e-6
                step = CHASE_SPEED * mul * dt
                L.x = wrap_x(L.x + dx / dist * step)
                L.y += dy / dist * step

            # enemy fire
            L.fire_cd = max(0.0, L.fire_cd - dt)
            if (L.fire_cd <= 0.0 and len(self.enemy_bullets) < MAX_ENEMY_BULLETS
                    and abs(self.player_y - L.y) < 0.12):
                dx = wrap_delta(self.player_x, L.x)
                if abs(dx) < ENEMY_FIRE_RANGE:
                    self.enemy_bullets.append(Bullet(L.x, L.y, 1.0 if dx > 0 else -1.0))
                    L.fire_cd = ENEMY_FIRE_COOLDOWN

    def _remove_lander(self, L: Lander, rescue: bool) -> None:
        if L.target is not None and L.target < len(self.humans):
            h = self.humans[L.target]
            h.claimed = False
            if rescue and L.state == "carry":
                h.alive = True
                h.x = wrap_x(L.x)
                self.events.append("rescue")
                self.score += SCORE_RESCUE
        if L in self.landers:
            self.landers.remove(L)

    def _collisions(self) -> None:
        # player bullets vs landers
        for b in list(self.bullets):
            for L in list(self.landers):
                if abs(wrap_delta(b.x, L.x)) < 0.05 and abs(b.y - L.y) < 0.05:
                    self._add_explosion(L.x, L.y)
                    self._remove_lander(L, rescue=True)
                    if b in self.bullets:
                        self.bullets.remove(b)
                    self.score += SCORE_KILL
                    self.events.append("kill")
                    break

        if self.invuln <= 0.0:
            # lander collides with player
            for L in list(self.landers):
                if (abs(wrap_delta(L.x, self.player_x)) < COLLIDE_R
                        and abs(L.y - self.player_y) < COLLIDE_R):
                    self._add_explosion(L.x, L.y)
                    self._remove_lander(L, rescue=True)
                    self._hit_player()
                    break
        if self.invuln <= 0.0:
            # enemy bullet hits player
            for b in list(self.enemy_bullets):
                if (abs(wrap_delta(b.x, self.player_x)) < 0.05
                        and abs(b.y - self.player_y) < 0.05):
                    self.enemy_bullets.remove(b)
                    self._hit_player()
                    break

    def _hit_player(self) -> None:
        self.lives -= 1
        self.invuln = INVULN_TIME
        self.events.append("hit")

    def _age_explosions(self, dt: float) -> None:
        for ex in list(self.explosions):
            ex.t += dt
            if not ex.alive:
                self.explosions.remove(ex)

    def _wave_check(self) -> None:
        if (self._spawned >= self._wave_total and not self.landers
                and self.state == DState.PLAY):
            self.score += SCORE_WAVE_HUMAN_BONUS * self._humans_alive()
            self.wave += 1
            self._start_wave()
            self.state = DState.BREAK
            self.break_timer = WAVE_BREAK_TIME
            self.events.append("wave")


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v
