"""The game itself: entities, physics, collisions, waves and scoring.

World coordinates are [-1, 1] on both axes and wrap toroidally (fly off one
edge, reappear on the opposite one). Nothing here knows anything about lasers
or pygame -- it is pure simulation, which keeps it easy to test and reason
about. `World.update()` advances one tick and fills `World.events` with
discrete sound cues for the app layer to play.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional

from engine.vec import Vec2, clampf

# --- world / gameplay tuning ------------------------------------------------
WORLD = 1.0                      # half-extent; playfield is [-1, 1]

SHIP_ACCEL = 1.7
SHIP_DRAG = 0.55                 # exponential velocity decay per second
SHIP_MAX_SPEED = 1.25
SHIP_ROT = 4.6                   # rad/s
SHIP_RADIUS = 0.055
SHIP_SCALE = 0.06                # drawing size
FIRE_COOLDOWN = 0.16
MAX_BULLETS = 4
BULLET_SPEED = 1.7
BULLET_LIFE = 0.72
BULLET_RADIUS = 0.01
SPAWN_INVULN = 2.6
HYPERSPACE_DEATH_CHANCE = 0.12

AST_RADIUS = {3: 0.165, 2: 0.093, 1: 0.05}
AST_SCORE = {3: 20, 2: 50, 1: 100}
AST_BASE_SPEED = 0.16
AST_SPIN = 1.2

SAUCER_BIG_RADIUS = 0.085
SAUCER_SMALL_RADIUS = 0.055
SAUCER_SPEED = 0.34
SAUCER_BIG_SCORE = 200
SAUCER_SMALL_SCORE = 1000
SAUCER_BULLET_SPEED = 1.15
SAUCER_BULLET_LIFE = 1.3

EXTRA_LIFE_EVERY = 10000
START_LIVES = 3
SAFE_SPAWN_RADIUS = 0.28         # centre must be this clear before respawn
# ...but only for so long. Big asteroids crawl (AST_BASE_SPEED) and the keep-out
# circle is SAFE_SPAWN_RADIUS + their radius, so one drifting over the middle
# can block a centre spawn for tens of seconds. After this much waiting the ship
# respawns in the middle regardless -- late is worse than tight, and the extra
# invulnerability below covers the risk.
RESPAWN_MAX_WAIT = 1.0
# A tight respawn extends invulnerability until whatever is crossing the middle
# has passed, instead of moving the ship somewhere safer.
MAX_SPAWN_INVULN = 6.0
INVULN_MARGIN = 0.6              # extra grace after the last rock clears


class State:
    ATTRACT = "attract"
    PLAYING = "playing"
    DYING = "dying"
    GAMEOVER = "gameover"


def wrap(p: Vec2) -> None:
    if p.x > WORLD:
        p.x -= 2 * WORLD
    elif p.x < -WORLD:
        p.x += 2 * WORLD
    if p.y > WORLD:
        p.y -= 2 * WORLD
    elif p.y < -WORLD:
        p.y += 2 * WORLD


def toro_dist(a: Vec2, b: Vec2) -> float:
    """Shortest distance between two points on the wrapping playfield."""
    dx = a.x - b.x
    dy = a.y - b.y
    if dx > WORLD:
        dx -= 2 * WORLD
    elif dx < -WORLD:
        dx += 2 * WORLD
    if dy > WORLD:
        dy -= 2 * WORLD
    elif dy < -WORLD:
        dy += 2 * WORLD
    return math.hypot(dx, dy)


@dataclass
class Input:
    turn: int = 0                # -1 left, +1 right
    thrust: bool = False
    fire: bool = False           # edge (just pressed)
    hyperspace: bool = False     # edge
    start: bool = False          # edge


class Ship:
    def __init__(self):
        self.pos = Vec2(0, 0)
        self.vel = Vec2(0, 0)
        self.angle = math.pi / 2   # facing up
        self.thrusting = False
        self.cooldown = 0.0
        self.invuln = SPAWN_INVULN

    def update(self, dt: float, inp: Input, world: "World") -> None:
        self.angle += inp.turn * SHIP_ROT * dt
        self.thrusting = inp.thrust
        if inp.thrust:
            self.vel += Vec2.from_angle(self.angle, SHIP_ACCEL * dt)
        self.vel = self.vel * math.exp(-SHIP_DRAG * dt)
        if self.vel.length() > SHIP_MAX_SPEED:
            self.vel = self.vel.with_length(SHIP_MAX_SPEED)
        self.pos += self.vel * dt
        wrap(self.pos)

        self.cooldown = max(0.0, self.cooldown - dt)
        self.invuln = max(0.0, self.invuln - dt)

        if inp.fire and self.cooldown <= 0 and len(world.bullets) < MAX_BULLETS:
            muzzle = Vec2.from_angle(self.angle, SHIP_RADIUS + 0.02)
            v = Vec2.from_angle(self.angle, BULLET_SPEED) + self.vel
            world.bullets.append(Bullet(self.pos + muzzle, v))
            self.cooldown = FIRE_COOLDOWN
            world.events.append("fire")

        if inp.hyperspace:
            self.hyperspace(world)

    def hyperspace(self, world: "World") -> None:
        self.pos = Vec2(random.uniform(-0.9, 0.9), random.uniform(-0.9, 0.9))
        self.vel = Vec2(0, 0)
        if random.random() < HYPERSPACE_DEATH_CHANCE:
            world.kill_ship()


class Bullet:
    def __init__(self, pos: Vec2, vel: Vec2):
        self.pos = pos.copy()
        self.vel = vel.copy()
        self.life = BULLET_LIFE

    def update(self, dt: float) -> None:
        self.pos += self.vel * dt
        wrap(self.pos)
        self.life -= dt


class Asteroid:
    def __init__(self, pos: Vec2, size: int, speed_scale: float = 1.0):
        self.pos = pos.copy()
        self.size = size
        self.radius = AST_RADIUS[size]
        speed = AST_BASE_SPEED * (1 + (3 - size) * 0.55) * speed_scale
        a = random.uniform(0, math.tau)
        self.vel = Vec2.from_angle(a, speed)
        self.shape_index = random.randrange(4)
        self.angle = random.uniform(0, math.tau)
        self.spin = random.uniform(-AST_SPIN, AST_SPIN)

    def update(self, dt: float) -> None:
        self.pos += self.vel * dt
        wrap(self.pos)
        self.angle += self.spin * dt


class Saucer:
    def __init__(self, small: bool, score: int):
        self.small = small
        self.radius = SAUCER_SMALL_RADIUS if small else SAUCER_BIG_RADIUS
        edge = random.choice((-1, 1))
        self.pos = Vec2(edge * (WORLD - 0.001), random.uniform(-0.7, 0.7))
        self.vel = Vec2(-edge * SAUCER_SPEED, 0)
        self.exit_dir = -edge
        self.fire_timer = random.uniform(0.7, 1.4)
        self.vstep_timer = 0.0
        self.score = score

    def update(self, dt: float, world: "World") -> None:
        self.vstep_timer -= dt
        if self.vstep_timer <= 0:
            self.vel.y = random.choice((-0.16, 0.0, 0.0, 0.16))
            self.vstep_timer = random.uniform(0.4, 0.9)
        self.pos += self.vel * dt
        # wrap vertically, exit horizontally
        if self.pos.y > WORLD:
            self.pos.y -= 2 * WORLD
        elif self.pos.y < -WORLD:
            self.pos.y += 2 * WORLD
        if (self.exit_dir > 0 and self.pos.x > WORLD + 0.05) or \
           (self.exit_dir < 0 and self.pos.x < -WORLD - 0.05):
            world.remove_saucer(scored=False)
            return

        self.fire_timer -= dt
        if self.fire_timer <= 0 and world.ship is not None:
            self.fire(world)
            self.fire_timer = random.uniform(0.9, 1.7)

    def fire(self, world: "World") -> None:
        if self.small:
            # Aim at the ship; accuracy improves as the score climbs.
            spread = clampf(0.5 - world.score / 60000.0, 0.03, 0.5)
            aim = (world.ship.pos - self.pos).angle() + random.uniform(-spread, spread)
        else:
            aim = random.uniform(0, math.tau)
        v = Vec2.from_angle(aim, SAUCER_BULLET_SPEED)
        world.saucer_bullets.append(Bullet(self.pos.copy(), v))
        world.saucer_bullets[-1].life = SAUCER_BULLET_LIFE
        world.events.append("saucer_fire")


class Debris:
    """A short line fragment thrown out by an explosion."""

    def __init__(self, pos: Vec2, colour):
        self.pos = pos.copy()
        a = random.uniform(0, math.tau)
        self.vel = Vec2.from_angle(a, random.uniform(0.15, 0.55))
        self.angle = random.uniform(0, math.tau)
        self.spin = random.uniform(-6, 6)
        self.len = random.uniform(0.02, 0.055)
        self.life = random.uniform(0.4, 0.9)
        self.max_life = self.life
        self.colour = colour

    def update(self, dt: float) -> None:
        self.pos += self.vel * dt
        wrap(self.pos)
        self.angle += self.spin * dt
        self.life -= dt


class World:
    def __init__(self):
        self.high_score = 0
        self.events: List[str] = []
        self.reset_attract()

    # -- lifecycle ----------------------------------------------------------
    def reset_attract(self) -> None:
        self.state = State.ATTRACT
        self.score = 0
        self.lives = 0
        self.wave = 0
        self.ship: Optional[Ship] = None
        self.bullets: List[Bullet] = []
        self.asteroids: List[Asteroid] = []
        self.saucer: Optional[Saucer] = None
        self.saucer_bullets: List[Bullet] = []
        self.debris: List[Debris] = []
        self.dying_timer = 0.0
        self.respawn_wait = 0.0
        self.wave_gap = 0.0
        self.wave_initial = 1
        self.saucer_timer = random.uniform(9, 16)
        self.beat_timer = 0.0
        self.beat_hi = False
        self.next_extra = EXTRA_LIFE_EVERY
        self._spawn_attract_field()

    def _spawn_attract_field(self) -> None:
        self.asteroids = []
        for _ in range(6):
            self.asteroids.append(Asteroid(self._edge_pos(), random.choice((2, 3))))

    def start_game(self) -> None:
        self.state = State.PLAYING
        self.score = 0
        self.lives = START_LIVES
        self.wave = 0
        self.bullets = []
        self.saucer = None
        self.saucer_bullets = []
        self.debris = []
        self.next_extra = EXTRA_LIFE_EVERY
        self.saucer_timer = random.uniform(9, 16)
        self._start_wave()
        self.ship = Ship()

    def _edge_pos(self) -> Vec2:
        """A random position near the border (away from the centre)."""
        if random.random() < 0.5:
            return Vec2(random.choice((-1, 1)) * random.uniform(0.6, 1.0),
                        random.uniform(-1, 1))
        return Vec2(random.uniform(-1, 1),
                    random.choice((-1, 1)) * random.uniform(0.6, 1.0))

    def _start_wave(self) -> None:
        self.wave += 1
        count = min(4 + self.wave, 11)
        self.wave_initial = count
        scale = 1.0 + 0.05 * (self.wave - 1)
        self.asteroids = [Asteroid(self._edge_pos(), 3, scale) for _ in range(count)]
        self.wave_gap = 0.0

    # -- damage / scoring ---------------------------------------------------
    def add_score(self, pts: int) -> None:
        self.score += pts
        if self.score >= self.next_extra:
            self.lives += 1
            self.next_extra += EXTRA_LIFE_EVERY
            self.events.append("extra_life")
        self.high_score = max(self.high_score, self.score)

    def split_asteroid(self, a: Asteroid) -> None:
        self.add_score(AST_SCORE[a.size])
        self.events.append({3: "bang_large", 2: "bang_med", 1: "bang_small"}[a.size])
        for _ in range(4):
            self.debris.append(Debris(a.pos, "debris"))
        if a.size > 1:
            scale = 1.0 + 0.05 * (self.wave - 1)
            self.asteroids.append(Asteroid(a.pos, a.size - 1, scale + 0.3))
            self.asteroids.append(Asteroid(a.pos, a.size - 1, scale + 0.3))

    def remove_saucer(self, scored: bool) -> None:
        if self.saucer is not None:
            if scored:
                self.add_score(self.saucer.score)
                self.events.append("bang_med")
                for _ in range(5):
                    self.debris.append(Debris(self.saucer.pos, "debris"))
            self.saucer = None
            self.events.append("saucer_gone")
        self.saucer_timer = random.uniform(8, 18)

    def kill_ship(self) -> None:
        if self.ship is None or self.state != State.PLAYING:
            return
        for _ in range(8):
            self.debris.append(Debris(self.ship.pos, "ship"))
        self.events.append("ship_explode")
        self.lives -= 1
        self.ship = None
        self.state = State.DYING
        self.dying_timer = 2.0
        self.respawn_wait = 0.0

    # -- main tick ----------------------------------------------------------
    def update(self, dt: float, inp: Input) -> None:
        self.events = []
        if self.state == State.ATTRACT:
            self._update_attract(dt, inp)
        elif self.state == State.PLAYING:
            self._update_playing(dt, inp)
        elif self.state == State.DYING:
            self._update_dying(dt, inp)
        elif self.state == State.GAMEOVER:
            self._update_gameover(dt, inp)

    def _update_attract(self, dt: float, inp: Input) -> None:
        for a in self.asteroids:
            a.update(dt)
        for d in list(self.debris):
            d.update(dt)
            if d.life <= 0:
                self.debris.remove(d)
        if inp.start:
            self.start_game()

    def _update_gameover(self, dt: float, inp: Input) -> None:
        for a in self.asteroids:
            a.update(dt)
        self.dying_timer -= dt
        if inp.start:
            self.start_game()
        elif self.dying_timer <= 0:
            self.reset_attract()

    def _update_dying(self, dt: float, inp: Input) -> None:
        for a in self.asteroids:
            a.update(dt)
        if self.saucer:
            self.saucer.update(dt, self)
        for b in list(self.saucer_bullets):
            b.update(dt)
            if b.life <= 0:
                self.saucer_bullets.remove(b)
        for d in list(self.debris):
            d.update(dt)
            if d.life <= 0:
                self.debris.remove(d)
        self.dying_timer -= dt
        if self.dying_timer <= 0:
            if self.lives <= 0:
                self.state = State.GAMEOVER
                self.dying_timer = 8.0
            else:
                self.respawn_wait += dt
                if self._centre_clear() or self.respawn_wait >= RESPAWN_MAX_WAIT:
                    self.ship = Ship()
                    self.ship.invuln = self._respawn_invuln()
                    self.state = State.PLAYING

    def _clearance(self, p: Vec2, t: float = 0.0) -> float:
        """Distance from p to the nearest hazard's edge, t seconds from now;
        negative if inside one."""
        gap = 99.0
        for a in self.asteroids:
            gap = min(gap, toro_dist(a.pos + a.vel * t, p) - a.radius)
        if self.saucer:
            gap = min(gap, toro_dist(self.saucer.pos + self.saucer.vel * t, p)
                      - self.saucer.radius)
        return gap

    def _respawn_invuln(self) -> float:
        """How long the fresh ship stays invulnerable (and flashing).

        Normally SPAWN_INVULN, but the ship always respawns dead centre, and
        the middle may still be busy when the wait runs out. So look ahead and
        hold invulnerability until the last rock crossing the centre has
        actually passed -- a tight respawn costs the player nothing but a
        longer blink.
        """
        c = Vec2(0, 0)
        danger = SHIP_RADIUS + 0.02
        last_bad = 0.0
        steps = int(MAX_SPAWN_INVULN / 0.1) + 1
        for i in range(steps):
            t = i * 0.1
            if self._clearance(c, t) < danger:
                last_bad = t
        if last_bad <= 0.0:
            return SPAWN_INVULN
        return min(MAX_SPAWN_INVULN, max(SPAWN_INVULN, last_bad + INVULN_MARGIN))

    def _centre_clear(self) -> bool:
        return self._clearance(Vec2(0, 0)) >= SAFE_SPAWN_RADIUS

    def _update_playing(self, dt: float, inp: Input) -> None:
        if self.ship:
            self.ship.update(dt, inp, self)

        for b in list(self.bullets):
            b.update(dt)
            if b.life <= 0:
                self.bullets.remove(b)
        for a in self.asteroids:
            a.update(dt)
        for b in list(self.saucer_bullets):
            b.update(dt)
            if b.life <= 0:
                self.saucer_bullets.remove(b)
        for d in list(self.debris):
            d.update(dt)
            if d.life <= 0:
                self.debris.remove(d)

        self._update_saucer(dt)
        self._collisions()
        self._beat(dt)

        if not self.asteroids and self.state == State.PLAYING:
            self.wave_gap += dt
            if self.wave_gap > 1.6:
                self._start_wave()

    def _update_saucer(self, dt: float) -> None:
        if self.saucer:
            self.saucer.update(dt, self)
        else:
            self.saucer_timer -= dt
            if self.saucer_timer <= 0 and self.asteroids:
                p_small = clampf(self.score / 40000.0, 0.15, 0.85)
                small = random.random() < p_small
                score = SAUCER_SMALL_SCORE if small else SAUCER_BIG_SCORE
                self.saucer = Saucer(small, score)
                self.events.append("saucer_small" if small else "saucer_big")

    def _collisions(self) -> None:
        # player bullets vs asteroids
        for b in list(self.bullets):
            hit = False
            for a in list(self.asteroids):
                if toro_dist(b.pos, a.pos) < a.radius + BULLET_RADIUS:
                    self.asteroids.remove(a)
                    self.split_asteroid(a)
                    hit = True
                    break
            if hit:
                if b in self.bullets:
                    self.bullets.remove(b)
                continue
            # player bullets vs saucer
            if self.saucer and toro_dist(b.pos, self.saucer.pos) < self.saucer.radius:
                self.bullets.remove(b)
                self.remove_saucer(scored=True)

        if self.ship is None:
            return
        vulnerable = self.ship.invuln <= 0

        # ship vs asteroids
        for a in self.asteroids:
            if toro_dist(self.ship.pos, a.pos) < a.radius + SHIP_RADIUS:
                if vulnerable:
                    self.asteroids.remove(a)
                    self.split_asteroid(a)
                    self.kill_ship()
                return

        # ship vs saucer
        if self.saucer and toro_dist(self.ship.pos, self.saucer.pos) < \
                self.saucer.radius + SHIP_RADIUS:
            if vulnerable:
                self.remove_saucer(scored=True)
                self.kill_ship()
            return

        # saucer bullets vs ship
        for b in self.saucer_bullets:
            if toro_dist(self.ship.pos, b.pos) < SHIP_RADIUS + BULLET_RADIUS:
                if vulnerable:
                    self.saucer_bullets.remove(b)
                    self.kill_ship()
                return

    def _beat(self, dt: float) -> None:
        self.beat_timer -= dt
        if self.beat_timer <= 0:
            self.beat_hi = not self.beat_hi
            self.events.append("beat_hi" if self.beat_hi else "beat_lo")
            frac = 1.0
            if self.wave_initial:
                frac = len(self.asteroids) / (self.wave_initial * 2)
            self.beat_timer = clampf(0.30 + 0.65 * min(frac, 1.0), 0.30, 0.95)
