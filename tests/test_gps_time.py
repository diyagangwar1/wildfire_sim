"""
Unit tests for gps_time.py utilities.

Run:  pytest tests/test_gps_time.py -v
"""

import os
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_time import utc_ns, utc_iso, is_fire_window, sleep_to_next_tick


class TestUtcNs(unittest.TestCase):
    def test_returns_int(self):
        self.assertIsInstance(utc_ns(), int)

    def test_positive(self):
        self.assertGreater(utc_ns(), 0)

    def test_strictly_increasing(self):
        t1 = utc_ns()
        time.sleep(0.01)
        t2 = utc_ns()
        self.assertGreater(t2, t1)

    def test_roughly_nanoseconds(self):
        # Should be in the ballpark of 1.7e18 ns (year ~2026)
        ns = utc_ns()
        self.assertGreater(ns, 1_700_000_000_000_000_000)
        self.assertLess(ns,    2_100_000_000_000_000_000)


class TestUtcIso(unittest.TestCase):
    def test_returns_string(self):
        self.assertIsInstance(utc_iso(), str)

    def test_contains_T_separator(self):
        self.assertIn("T", utc_iso())

    def test_contains_Z_suffix(self):
        self.assertIn("Z", utc_iso())


class TestIsFireWindow(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(is_fire_window(), bool)

    def test_always_on_when_full_duty_cycle(self):
        # on_s == cycle_s → t % cycle_s is always in [0, on_s)
        self.assertTrue(is_fire_window(cycle_s=10.0, on_s=10.0))

    def test_always_off_when_zero_duty_cycle(self):
        self.assertFalse(is_fire_window(cycle_s=10.0, on_s=0.0))

    def test_both_workers_see_same_state(self):
        # Since is_fire_window() depends only on wall-clock, two immediate
        # calls must return the same value (correlated fire model requirement).
        a = is_fire_window()
        b = is_fire_window()
        self.assertEqual(a, b)


class TestSleepToNextTick(unittest.TestCase):
    def test_sleeps_at_most_one_period(self):
        period = 0.15
        t0 = time.time()
        sleep_to_next_tick(period)
        elapsed = time.time() - t0
        self.assertLessEqual(elapsed, period + 0.05,
                             f"Slept {elapsed:.3f}s, expected <= {period + 0.05:.3f}s")

    def test_wakes_near_period_boundary(self):
        period = 0.1
        sleep_to_next_tick(period)
        t = time.time()
        remainder = t % period
        # Allow up to 30 ms of OS scheduling jitter
        self.assertLess(remainder, 0.03,
                        f"Woke {remainder*1000:.1f}ms past the boundary (expected < 30ms)")

    def test_two_calls_align_to_same_grid(self):
        period = 0.2
        sleep_to_next_tick(period)
        t1 = time.time()
        sleep_to_next_tick(period)
        t2 = time.time()
        gap = t2 - t1
        # Should be approximately one period (within 50 ms)
        self.assertAlmostEqual(gap, period, delta=0.05,
                               msg=f"Gap between ticks was {gap:.3f}s, expected ~{period}s")


if __name__ == "__main__":
    unittest.main()
