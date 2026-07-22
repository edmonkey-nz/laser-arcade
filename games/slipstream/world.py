"""SLIPSTREAM -- a Wipeout-style hover racer, as pure logic.

The camera sits just behind and above the craft, looking down a track that
recedes to a horizon. You steer left/right, hold the accelerator, and tap the
drift-brake to break grip and slide the nose through tight bends. Clip a wall
and you scrape (sparks + speed bleed). Ramps launch you over gaps.

It's a time-trial: each track is timed, your best per level is remembered for
the session, and you can retry a track to beat your time or move up to the next
(harder) one. No pygame, no laser in here -- just the model.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List

# --- track sampling / camera (screen units live in [-1, 1]) ----------------
DRAW = 34                 # segments drawn ahead
ROADW_NEAR = 0.92         # half road width at the nearest segment (screen)
HORIZON_Y = 0.32          # where the track vanishes
NEAR_Y = -0.90            # screen y of the nearest segment
CAM_K = 0.130             # perspective falloff per segment
CURVE_X = 0.0044          # world-curve -> screen-x gain
HILL_Y = 0.019            # altitude -> screen-y gain
RUNG_SPACING = 6

# --- driving feel ----------------------------------------------------------
MAX_X = 1.0               # track edge in half-widths; beyond = wall
OFF_MAX = 1.18            # hard limit past the wall
BASE_TOP = 46.0           # top speed (segments/sec) at level 0
ACCEL = 34.0
DRAG = 16.0               # coasting deceleration
BRAKE = 60.0
SCRAPE_DECEL = 80.0
STEER = 2.4               # lateral response (half-widths/sec at full stick)
CENTRIFUGAL = 0.052       # outward push in a bend, scaled by speed -- strong
DRIFT_CF = 1.8            # extra push while drifting
JUMP_MIN = 20.0           # min speed to actually launch off a ramp
AIR_TIME = 0.62           # seconds airborne on a good launch
HIT_HALF = 0.12           # obstacle+craft half-width for a collision
CRASH_MULT = 0.35         # speed kept after hitting an obstacle

# --- hull integrity --------------------------------------------------------
MAX_HEALTH = 100.0
DMG_CRASH = 34.0          # per obstacle hit (~3 kills you)
DMG_SCRAPE = 26.0         # per second grinding a wall
DMG_GAP = 16.0            # per gap segment fallen into (missed a ramp)
DMG_HARD_LAND = 14.0      # landing a jump off the track


class RState:
    READY = "ready"
    RACE = "race"
    FINISH = "finish"
    DEAD = "dead"


@dataclass
class RacerInput:
    steer: float = 0.0        # -1 left .. +1 right
    accel: bool = False
    brake: bool = False       # brake / drift
    start: bool = False       # Enter: begin / next track
    retry: bool = False       # R: replay this track


@dataclass
class Track:
    curve: List[float]
    hill: List[float]
    gap: List[bool]
    take: List[bool]          # takeoff (ramp lip) segment
    obstacle: List[float]     # lateral position of an obstacle, or None
    length: int
    level: int


def build_track(level: int) -> Track:
    """Deterministic per level, so 'retry' is the same track. Each level is
    longer, curvier, hillier and busier than the last. The mix is guaranteed
    (hairpins, crests, ramps and obstacles all appear), not left to chance.
    """
    rng = random.Random(2000 + level)
    curve: List[float] = []
    hill: List[float] = []
    gap: List[bool] = []
    take: List[bool] = []
    obstacle: List[float] = []

    def emit(n, c=0.0, h=0.0, is_gap=False, is_take=False):
        for k in range(n):
            curve.append(c)
            hill.append(h)
            gap.append(is_gap)
            take.append(is_take and k == 0)
            obstacle.append(None)

    def curve_feature(n, peak):
        # single-sign: a sustained bend that turns and straightens
        for k in range(n):
            emit(1, c=peak * math.sin(math.pi * (k + 0.5) / n))

    def hill_feature(n, peak):
        # full period: rise and fall -- a crest you go up and over
        for k in range(n):
            emit(1, h=peak * math.sin(2.0 * math.pi * (k + 0.5) / n))

    def jump():
        emit(6, h=hmag * 0.9)
        emit(1, h=hmag * 0.9, is_take=True)
        emit(rng.randint(3, 5), is_gap=True)
        emit(rng.randint(10, 16))

    cmag = 0.9 * (1.0 + 0.14 * level)
    hmag = 1.6 * (1.0 + 0.12 * level)

    # ~3x the old length: many more features per level
    n_curves = 8 + 2 * level
    n_hairpins = 2 + level
    n_hills = 4 + level
    n_jumps = 1 + level
    feats = []
    for k in range(n_curves):
        feats.append(("curve", 1 if k % 2 == 0 else -1))
    for k in range(n_hairpins):
        feats.append(("hairpin", 1 if k % 2 == 0 else -1))
    feats += [("hill", 0)] * n_hills
    feats += [("jump", 0)] * n_jumps
    rng.shuffle(feats)

    emit(26)                               # start straight
    for kind, dirn in feats:
        if kind == "curve":
            curve_feature(rng.randint(22, 38),
                          dirn * cmag * rng.uniform(0.6, 1.0))
            emit(rng.randint(8, 16))
        elif kind == "hairpin":
            # long and sharp: you have to lift off / brake to hold it
            curve_feature(rng.randint(34, 50),
                          dirn * cmag * rng.uniform(1.7, 2.2))
            emit(rng.randint(10, 18))
        elif kind == "hill":
            big = rng.random() < 0.4
            hill_feature(rng.randint(18, 30),
                         rng.choice((-1, 1)) * hmag * (1.6 if big else 1.0) * rng.uniform(0.6, 1.0))
            emit(rng.randint(6, 12))
        else:
            jump()
    emit(34)                               # run-in to the finish

    length = len(curve)

    # scatter obstacles on drivable straight-ish segments (not near ramps/gaps)
    n_obstacles = 2 + level
    placed = 0
    tries = 0
    guard = set()
    while placed < n_obstacles and tries < n_obstacles * 40:
        tries += 1
        s = rng.randint(30, length - 40)
        if gap[s] or take[s] or obstacle[s] is not None:
            continue
        if any((s + o) in guard for o in range(-4, 5)):
            continue
        obstacle[s] = round(rng.uniform(-0.62, 0.62), 3)
        for o in range(-4, 5):
            guard.add(s + o)
        placed += 1

    return Track(curve, hill, gap, take, obstacle, length, level)


@dataclass
class RacerWorld:
    level: int = 0
    state: str = RState.READY
    pos: float = 0.0          # segment position along the track
    player_x: float = 0.0     # lateral, in half-widths (-1..1 = on track)
    speed: float = 0.0
    time: float = 0.0
    airborne: float = 0.0     # seconds of air remaining
    air_t: float = 0.0        # elapsed air (for the arc)
    scraping: bool = False
    new_best: bool = False
    lean: float = 0.0         # visual bank (-1..1), follows steering
    health: float = MAX_HEALTH
    best: dict = field(default_factory=dict)   # level -> best time (session)
    events: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.track = build_track(self.level)
        self._reset_run()

    # -- setup --------------------------------------------------------------
    def _reset_run(self):
        self.pos = 0.0
        self.player_x = 0.0
        self.speed = 0.0
        self.time = 0.0
        self.airborne = 0.0
        self.air_t = 0.0
        self.scraping = False
        self.new_best = False
        self.health = MAX_HEALTH
        self._was_gap = False
        self.state = RState.READY

    def load_level(self, level: int):
        self.level = max(0, level)
        self.track = build_track(self.level)
        self._reset_run()

    @property
    def top_speed(self) -> float:
        return BASE_TOP * (1.0 + 0.06 * self.level)

    @property
    def finish_pos(self) -> float:
        return self.track.length - 2

    def best_time(self):
        return self.best.get(self.level)

    # -- step ---------------------------------------------------------------
    def update(self, dt: float, inp: RacerInput) -> None:
        self.events = []
        if self.state == RState.READY:
            if inp.start:
                self.state = RState.RACE
            return
        if self.state == RState.FINISH:
            if inp.retry:
                self.load_level(self.level)
            elif inp.start:
                self.load_level(self.level + 1)
            return
        if self.state == RState.DEAD:
            if inp.start or inp.retry:
                self.load_level(self.level)     # wrecked -> retry this track
            return

        # ---- RACE ----
        self.time += dt
        airborne = self.airborne > 0.0

        seg = self.track.curve[min(int(self.pos), self.track.length - 1)]

        # steering + grip; drifting (brake) loosens the back end so the nose
        # slides wide through a bend instead of tracking cleanly
        steer_gain = STEER * (0.55 if airborne else 1.0) * (1.15 if inp.brake else 1.0)
        self.player_x += inp.steer * steer_gain * dt
        if not airborne:
            cf = CENTRIFUGAL * (DRIFT_CF if inp.brake else 1.0)
            self.player_x += seg * self.speed * cf * dt
        # visual bank follows the stick (plus a little from the bend)
        target_lean = _clamp(inp.steer + seg * self.speed * 0.02 * (2.0 if inp.brake else 1.0), -1.4, 1.4)
        self.lean += (target_lean - self.lean) * min(1.0, 9.0 * dt)

        # wall contact: pinned to the edge, no thrust while grinding, and a
        # brutal speed bleed -- so just holding the gas through a bend without
        # steering scrubs off almost all your speed
        self.scraping = abs(self.player_x) > MAX_X
        if self.scraping and abs(self.player_x) > OFF_MAX:
            self.player_x = math.copysign(OFF_MAX, self.player_x)

        # speed
        if self.scraping:
            self.speed -= SCRAPE_DECEL * dt
        else:
            if inp.accel:
                self.speed += ACCEL * dt
            else:
                self.speed -= DRAG * dt
            if inp.brake:
                self.speed -= BRAKE * dt
        self.speed = _clamp(self.speed, 0.0, self.top_speed)
        if self.scraping:
            self.health -= DMG_SCRAPE * dt

        # advance
        prev_idx = int(self.pos)
        self.pos += self.speed * dt

        # obstacles crossed this frame (checked per whole segment, so you can't
        # skip through one at speed)
        end_idx = int(self.pos)
        for s in range(prev_idx + 1, end_idx + 1):
            if s >= self.track.length:
                break
            lat = self.track.obstacle[s]
            if lat is not None and abs(self.player_x - lat) < HIT_HALF:
                self.speed *= CRASH_MULT
                self.player_x += math.copysign(0.4, (self.player_x - lat) or 1.0)
                self.player_x = _clamp(self.player_x, -OFF_MAX, OFF_MAX)
                self.health -= DMG_CRASH
                self.events.append("crash")
                break

        # ramps & gaps
        idx = min(int(self.pos), self.track.length - 1)
        if self.airborne > 0.0:
            self.airborne -= dt
            self.air_t += dt
            if self.airborne <= 0.0:
                self.airborne = 0.0
                self.events.append("land")
                if abs(self.player_x) > MAX_X:
                    self.events.append("scrape")
                    self.health -= DMG_HARD_LAND
            self._was_gap = False
        else:
            if self.track.take[idx] and self.speed >= JUMP_MIN:
                self.airborne = AIR_TIME
                self.air_t = 0.0
                self.events.append("launch")
                self._was_gap = False
            elif self.track.gap[idx]:
                # dropped into the gap -- heavy bleed, a clunk, and damage once
                self.speed -= SCRAPE_DECEL * 1.5 * dt
                self.speed = max(self.speed, 0.0)
                if not self._was_gap:
                    self.health -= DMG_GAP
                    self.events.append("bump")
                self._was_gap = True
            else:
                self._was_gap = False

        # hull destroyed -> forced loss
        if self.health <= 0.0 and self.state == RState.RACE:
            self.health = 0.0
            self.state = RState.DEAD
            self.events.append("explode")
            return

        # finish
        if self.state == RState.RACE and self.pos >= self.finish_pos:
            self.pos = self.finish_pos
            self.state = RState.FINISH
            prev = self.best.get(self.level)
            if prev is None or self.time < prev:
                self.best[self.level] = self.time
                self.new_best = True
            self.events.append("finish")

    # -- for the renderer ---------------------------------------------------
    def speed_frac(self) -> float:
        return 0.0 if self.top_speed <= 0 else self.speed / self.top_speed

    def health_frac(self) -> float:
        return max(0.0, min(1.0, self.health / MAX_HEALTH))


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v
