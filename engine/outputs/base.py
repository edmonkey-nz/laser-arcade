"""Common interface for anything that consumes a planned laser frame."""
from __future__ import annotations

from typing import List, Tuple

LaserPoint = Tuple[int, int, int, int, int]


class Output:
    def start(self) -> bool:
        """Initialise. Return True on success."""
        return True

    def send(self, points: List[LaserPoint], pps: int) -> None:
        """Consume one planned frame (points in DAC units 0..4095)."""
        raise NotImplementedError

    def close(self) -> None:
        pass
