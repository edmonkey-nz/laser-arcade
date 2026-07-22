"""A tiny 2D vector. Small and allocation-light; asteroids never has enough
entities for this to be a bottleneck, and readable math beats micro-tuning."""
from __future__ import annotations

import math


class Vec2:
    __slots__ = ("x", "y")

    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.x = float(x)
        self.y = float(y)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_angle(cls, a: float, length: float = 1.0) -> "Vec2":
        return cls(math.cos(a) * length, math.sin(a) * length)

    def copy(self) -> "Vec2":
        return Vec2(self.x, self.y)

    # -- arithmetic ---------------------------------------------------------
    def __add__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, s: float) -> "Vec2":
        return Vec2(self.x * s, self.y * s)

    __rmul__ = __mul__

    def __iadd__(self, o: "Vec2") -> "Vec2":
        self.x += o.x
        self.y += o.y
        return self

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"Vec2({self.x:.3f}, {self.y:.3f})"

    # -- geometry -----------------------------------------------------------
    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def length_sq(self) -> float:
        return self.x * self.x + self.y * self.y

    def angle(self) -> float:
        return math.atan2(self.y, self.x)

    def rotated(self, a: float) -> "Vec2":
        c, s = math.cos(a), math.sin(a)
        return Vec2(self.x * c - self.y * s, self.x * s + self.y * c)

    def with_length(self, n: float) -> "Vec2":
        l = self.length()
        if l == 0:
            return Vec2()
        k = n / l
        return Vec2(self.x * k, self.y * k)


def clampf(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
