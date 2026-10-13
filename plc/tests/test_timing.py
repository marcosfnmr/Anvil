from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timing import ResponseJitter  # noqa: E402


class ResponseJitterTests(unittest.TestCase):
    def test_legacy_deterministic_profile_keeps_its_sequence(self):
        jitter = ResponseJitter(50)
        self.assertEqual([jitter.next_ms() for _ in range(4)], [3, 43, 50, 4])

    def test_uniform_profile_is_bounded_and_seed_reproducible(self):
        first = ResponseJitter(60, seed=17, distribution="uniform")
        second = ResponseJitter(60, seed=17, distribution="uniform")
        values = [first.next_ms() for _ in range(1000)]
        self.assertEqual(values, [second.next_ms() for _ in range(1000)])
        self.assertTrue(all(0 <= value <= 60 for value in values))
        self.assertTrue(any(value == 0 for value in values))
        self.assertTrue(any(value == 60 for value in values))

    def test_normal_profile_is_bounded_reproducible_and_centered(self):
        first = ResponseJitter(60, seed=29, distribution="normal")
        second = ResponseJitter(60, seed=29, distribution="normal")
        values = [first.next_ms() for _ in range(10000)]
        self.assertEqual(values, [second.next_ms() for _ in range(10000)])
        self.assertTrue(all(0 <= value <= 60 for value in values))
        self.assertAlmostEqual(sum(values) / len(values), 30, delta=1)
        self.assertGreater(len(set(values)), 20)

    def test_invalid_distribution_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported response jitter distribution"):
            ResponseJitter(10, distribution="exponential")


if __name__ == "__main__":
    unittest.main()
