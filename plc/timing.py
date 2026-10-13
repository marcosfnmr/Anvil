"""Reproducible bounded response jitter shared by protocol runtimes."""

from __future__ import annotations

import random
from typing import Literal


class ResponseJitter:
    """Return seeded delays in [0, maximum_ms] using a selectable profile.

    The normal profile is centered at half the configured maximum, with a
    standard deviation of maximum/6, then clipped to the configured bounds.
    """

    def __init__(self, maximum_ms: int, seed: int = 0,
                 distribution: Literal["deterministic", "uniform", "normal"] = "deterministic") -> None:
        if distribution not in {"deterministic", "uniform", "normal"}:
            raise ValueError(f"unsupported response jitter distribution: {distribution}")
        self.maximum_ms = maximum_ms
        self.distribution = distribution
        self._state = seed & 0x7FFFFFFF
        self._random = random.Random(seed)

    def next_ms(self) -> int:
        if not self.maximum_ms:
            return 0
        if self.distribution == "deterministic":
            self._state = (1103515245 * self._state + 12345) & 0x7FFFFFFF
            return self._state % (self.maximum_ms + 1)
        if self.distribution == "uniform":
            return self._random.randint(0, self.maximum_ms)
        value = self._random.gauss(self.maximum_ms / 2, self.maximum_ms / 6)
        return min(self.maximum_ms, max(0, round(value)))


# Backwards-compatible name for existing tests and callers.
DeterministicJitter = ResponseJitter
