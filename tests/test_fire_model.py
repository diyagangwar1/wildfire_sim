"""
Unit tests for fire_model.py — the cellular automaton fire spread model.

Coverage:
- FireGrid initialises with exactly one burning cell at the ignition point
- step() causes fire to spread (burning count grows)
- step() causes cells to burn out (BURNED count grows)
- detection_prob() scales correctly: 0 cells → FALSE_ALARM_PROB, more cells → higher
- burning_near() counts only cells within radius, not the whole grid
- advance_to_wall_clock_step() is idempotent (re-calling doesn't rewind)
- Two FireGrid instances with same seed produce identical grids after N steps
- Two FireGrid instances with different seeds eventually diverge
- is_any_burning() / total_burning() / total_burned() stay consistent

Run:  python3 -m unittest tests/test_fire_model.py -v
"""

import math
import os
import sys
import time
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fire_model import (
    FireGrid,
    IGNITION_XY_M, GRID_CELLS, CELL_M, GRID_HALF_M,
    FIRE_STEP_INTERVAL_S, DETECTION_RADIUS_M,
    DETECT_MAX_P, FALSE_ALARM_PROB,
    UNBURNED, BURNING, BURNED,
)


class TestFireGridInit(unittest.TestCase):

    def setUp(self):
        self.fg = FireGrid(seed=0)

    def test_exactly_one_burning_cell_at_start(self):
        self.assertEqual(self.fg.total_burning(), 1)

    def test_no_burned_cells_at_start(self):
        self.assertEqual(self.fg.total_burned(), 0)

    def test_is_burning_at_start(self):
        self.assertTrue(self.fg.is_any_burning())

    def test_ignition_cell_is_burning(self):
        """The cell at IGNITION_XY_M must be BURNING after init."""
        ix = int((IGNITION_XY_M[0] + GRID_HALF_M) / CELL_M)
        iy = int((IGNITION_XY_M[1] + GRID_HALF_M) / CELL_M)
        self.assertEqual(self.fg._grid[iy][ix], BURNING)

    def test_step_counter_starts_at_zero(self):
        self.assertEqual(self.fg.t, 0)


class TestFireSpread(unittest.TestCase):

    def test_fire_spreads_over_time(self):
        """After many steps the fire should cover more cells than at start."""
        fg = FireGrid(seed=0)
        initial = fg.total_burning()
        for _ in range(30):
            fg.step()
        self.assertGreater(fg.total_burning() + fg.total_burned(), initial,
                           "Fire should spread to more cells over 30 steps")

    def test_burned_cells_accumulate(self):
        """BURNED count must be non-decreasing."""
        fg = FireGrid(seed=0)
        prev_burned = 0
        for _ in range(40):
            fg.step()
            burned = fg.total_burned()
            self.assertGreaterEqual(burned, prev_burned,
                                    "total_burned() should never decrease")
            prev_burned = burned

    def test_step_increments_t(self):
        fg = FireGrid(seed=0)
        for i in range(1, 6):
            fg.step()
            self.assertEqual(fg.t, i)

    def test_grid_only_contains_valid_states(self):
        fg = FireGrid(seed=42)
        for _ in range(20):
            fg.step()
        valid = {UNBURNED, BURNING, BURNED}
        for row in fg._grid:
            for cell in row:
                self.assertIn(cell, valid, f"Invalid cell state: {cell}")

    def test_fire_eventually_extinguishes(self):
        """With burnout probability > 0 and finite grid, fire should eventually go out."""
        fg = FireGrid(seed=7)
        for _ in range(600):    # seed=7 extinguishes around step 421
            fg.step()
            if not fg.is_any_burning():
                break
        self.assertFalse(fg.is_any_burning(),
                         "Fire should eventually go out (burnout_prob > 0, finite grid)")


class TestDetectionProb(unittest.TestCase):

    def test_false_alarm_floor_when_no_burning_near(self):
        """A drone far from the fire should get exactly FALSE_ALARM_PROB."""
        fg = FireGrid(seed=0)
        # No steps taken, fire is at (20, 20) — test from far corner
        p = fg.detection_prob((-40.0, -40.0))
        self.assertAlmostEqual(p, FALSE_ALARM_PROB, places=5)

    def test_detection_increases_near_burning_cells(self):
        """A drone near many burning cells should have higher detection prob than far."""
        fg = FireGrid(seed=0)
        for _ in range(50):
            fg.step()
        near_p  = fg.detection_prob((IGNITION_XY_M[0], IGNITION_XY_M[1]))
        far_p   = fg.detection_prob((-40.0, -40.0))
        self.assertGreater(near_p, far_p)

    def test_prob_bounded_0_1(self):
        fg = FireGrid(seed=0)
        for _ in range(30):
            fg.step()
        for xy in [(20, 20), (0, 0), (-30, -30), (40, 40)]:
            p = fg.detection_prob(xy)
            self.assertGreaterEqual(p, 0.0, f"prob < 0 at {xy}")
            self.assertLessEqual(p, 1.0,    f"prob > 1 at {xy}")

    def test_prob_never_below_false_alarm_floor(self):
        fg = FireGrid(seed=0)
        for _ in range(10):
            fg.step()
        for xy in [(20, 20), (-40, -40), (0, 0)]:
            p = fg.detection_prob(xy)
            self.assertGreaterEqual(p, FALSE_ALARM_PROB,
                                    f"prob {p:.4f} below FALSE_ALARM_PROB at {xy}")

    def test_prob_approaches_max_when_covered_in_fire(self):
        """After a long run with lots of fire, prob near ignition should be near DETECT_MAX_P."""
        fg = FireGrid(seed=0)
        for _ in range(80):
            fg.step()
        if fg.total_burning() > 10:   # only check if fire is still going
            p = fg.detection_prob((IGNITION_XY_M[0], IGNITION_XY_M[1]))
            self.assertGreater(p, 0.50,
                               "Detection prob should be high when many cells are burning")


class TestBurningNear(unittest.TestCase):

    def test_zero_count_when_fire_far(self):
        fg = FireGrid(seed=0)
        # Fire just ignited at (20, 20); check from (-40, -40) with small radius
        count = fg.burning_near(-40.0, -40.0, radius_m=5.0)
        self.assertEqual(count, 0)

    def test_nonzero_count_near_ignition(self):
        fg = FireGrid(seed=0)
        count = fg.burning_near(IGNITION_XY_M[0], IGNITION_XY_M[1], radius_m=2.0)
        self.assertGreater(count, 0, "Should see at least 1 burning cell at ignition point")

    def test_count_increases_with_radius(self):
        fg = FireGrid(seed=0)
        for _ in range(20):
            fg.step()
        small_r = fg.burning_near(IGNITION_XY_M[0], IGNITION_XY_M[1], radius_m=5.0)
        large_r = fg.burning_near(IGNITION_XY_M[0], IGNITION_XY_M[1], radius_m=30.0)
        self.assertGreaterEqual(large_r, small_r,
                                "Larger radius must see >= cells as smaller radius")


class TestDeterminism(unittest.TestCase):

    def test_same_seed_produces_same_grid(self):
        """Two instances with same seed must have identical grids after N steps."""
        n_steps = 30
        fg1 = FireGrid(seed=99)
        fg2 = FireGrid(seed=99)
        for _ in range(n_steps):
            fg1.step()
            fg2.step()
        self.assertEqual(fg1.t, fg2.t)
        self.assertEqual(fg1._grid, fg2._grid,
                         "Same-seed grids should be identical after same number of steps")

    def test_different_seeds_produce_different_grids(self):
        """After enough steps, two different seeds should diverge."""
        fg1 = FireGrid(seed=1)
        fg2 = FireGrid(seed=2)
        for _ in range(40):
            fg1.step()
            fg2.step()
        # Grids should differ somewhere
        self.assertNotEqual(fg1._grid, fg2._grid,
                            "Different-seed grids should diverge over 40 steps")

    def test_advance_to_step_idempotent(self):
        """
        advance_to_wall_clock_step() should never rewind t.
        We mock time.time() so the target is a small, finite step count.
        """
        fg = FireGrid(seed=0)
        # Mock time so target = int(30.0 / 0.5) = 60 steps — tiny and fast
        with patch("fire_model.time") as mock_time:
            mock_time.time.return_value = 30.0
            fg.advance_to_wall_clock_step()
            t_after_first = fg.t
            self.assertEqual(t_after_first, 60,
                             "Should reach exactly 60 steps with mocked time=30s")

            # Call again at same time — t must not decrease
            fg.advance_to_wall_clock_step()
            self.assertEqual(fg.t, t_after_first,
                             "Second call at same time should be a no-op")


class TestWallClockSync(unittest.TestCase):
    """
    Verify that two FireGrid instances with the same seed reach identical state
    when both are advanced via advance_to_wall_clock_step() at the same time.
    This mirrors how both workers sync during a Mininet run.
    """

    def test_two_grids_sync_via_wall_clock(self):
        # Mock time so both grids advance to the same small target step
        with patch("fire_model.time") as mock_time:
            mock_time.time.return_value = 25.0   # target = int(25/0.5) = 50 steps
            fg1 = FireGrid(seed=0)
            fg2 = FireGrid(seed=0)
            fg1.advance_to_wall_clock_step()
            fg2.advance_to_wall_clock_step()
        self.assertEqual(fg1.t, fg2.t,
                         "Both grids should be at the same step after wall-clock advance")
        self.assertEqual(fg1._grid, fg2._grid,
                         "Both grids should be identical after wall-clock advance")


if __name__ == "__main__":
    unittest.main()
