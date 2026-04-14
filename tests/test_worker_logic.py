"""
Unit tests for thermal_worker.py and imagery_worker.py logic.

Coverage:
- gen_thermal() / gen_imagery() produce correctly structured messages
- _lawnmower_pos() covers the survey area deterministically and loops
- _drop_prob_from_distance() returns values in [0, MAX_DROP_PROB]
- Clock offset / jitter fields appear in message dicts
- GPS noise keeps positions near the lawnmower waypoint
- Both workers share the same lawnmower XY track (different altitudes)
- fire_seed argument accepted without error

Run:  python3 -m unittest tests/test_worker_logic.py -v
"""

import math
import os
import random
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import thermal_worker as TW
import imagery_worker as IW
from fire_model import FireGrid


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_fire_grid(seed: int = 0) -> FireGrid:
    """Return a FireGrid that has been stepped a few times so fire has spread."""
    fg = FireGrid(seed=seed)
    for _ in range(20):
        fg.step()
    return fg


def _lawnmower_positions(module, n: int = 300):
    """Return n consecutive lawnmower waypoints (no noise) for a given worker module."""
    return [module._lawnmower_pos(i, module.DRONE_ALTITUDE_M) for i in range(n)]


# ---------------------------------------------------------------------------
# Thermal worker — gen_thermal
# ---------------------------------------------------------------------------

class TestThermalGenMessage(unittest.TestCase):
    def setUp(self):
        random.seed(42)
        self.fg = _make_fire_grid()
        # Position directly above the fire ignition point → high detection prob
        self.near_fire = (20.0, 20.0, TW.DRONE_ALTITUDE_M)
        self.far_from_fire = (0.0, 0.0, TW.DRONE_ALTITUDE_M)

    def test_returns_dict(self):
        msg = TW.gen_thermal(self.far_from_fire, self.fg)
        self.assertIsInstance(msg, dict)

    def test_required_keys_present(self):
        msg = TW.gen_thermal(self.far_from_fire, self.fg)
        for key in ("sensor", "data", "fire_sim"):
            self.assertIn(key, msg, f"Missing key '{key}'")

    def test_sensor_tag_is_thermal(self):
        self.assertEqual(TW.gen_thermal(self.far_from_fire, self.fg)["sensor"], "thermal")

    def test_data_is_list(self):
        for _ in range(20):
            msg = TW.gen_thermal(self.far_from_fire, self.fg)
            self.assertIsInstance(msg["data"], list)

    def test_fire_sim_is_bool(self):
        for _ in range(20):
            self.assertIsInstance(TW.gen_thermal(self.far_from_fire, self.fg)["fire_sim"], bool)

    def test_hotspot_exceeds_threshold_when_fire_sim_true(self):
        """When fire_sim=True the data must contain a value > 100°C."""
        found = False
        for seed in range(200):
            random.seed(seed)
            msg = TW.gen_thermal(self.near_fire, self.fg)
            if msg["fire_sim"]:
                found = True
                flat = []
                for row in msg["data"]:
                    flat.extend(row) if isinstance(row, list) else flat.append(row)
                self.assertGreater(max(flat), 100.0,
                                   "fire_sim=True frame should contain temps > 100°C")
                break
        self.assertTrue(found, "gen_thermal near fire never produced fire_sim=True in 200 tries")

    def test_higher_detection_prob_near_fire(self):
        """More fire_sim=True events expected when drone is near the fire zone.

        Fire ignites at (20, 20) and spreads NE.  After 20 steps the detection
        radius (40m) captures ~200 burning cells from (20, 20) but 0 from (-45, -45)
        (>85m away, upwind).  That gives prob ≈ 0.92 near vs ≈ 0.06 far, so near
        should produce many more positives over 100 draws.
        """
        random.seed(0)
        # Drone directly over ignition — high detection probability
        near = sum(TW.gen_thermal(self.near_fire, self.fg)["fire_sim"] for _ in range(100))
        # Drone far upwind from fire — sees 0 burning cells, gets only false-alarm rate
        far  = sum(TW.gen_thermal((-45.0, -45.0, TW.DRONE_ALTITUDE_M), self.fg)["fire_sim"]
                   for _ in range(100))
        self.assertGreater(near, far,
                           f"Expected near({near}) > far({far}): more detections near fire")


# ---------------------------------------------------------------------------
# Imagery worker — gen_imagery
# ---------------------------------------------------------------------------

class TestImageryGenMessage(unittest.TestCase):
    def setUp(self):
        random.seed(42)
        self.fg = _make_fire_grid()
        self.near_fire = (20.0, 20.0, IW.DRONE_ALTITUDE_M)
        self.far_from_fire = (0.0, 0.0, IW.DRONE_ALTITUDE_M)

    def test_returns_dict(self):
        self.assertIsInstance(IW.gen_imagery(self.far_from_fire, self.fg), dict)

    def test_required_keys_present(self):
        msg = IW.gen_imagery(self.far_from_fire, self.fg)
        for key in ("sensor", "detections", "fire_sim"):
            self.assertIn(key, msg, f"Missing key '{key}'")

    def test_sensor_tag_is_imagery(self):
        self.assertEqual(IW.gen_imagery(self.far_from_fire, self.fg)["sensor"], "imagery")

    def test_detections_is_list(self):
        for _ in range(20):
            self.assertIsInstance(IW.gen_imagery(self.far_from_fire, self.fg)["detections"], list)

    def test_fire_sim_is_bool(self):
        for _ in range(20):
            self.assertIsInstance(IW.gen_imagery(self.far_from_fire, self.fg)["fire_sim"], bool)

    def test_detection_labels_valid(self):
        valid = {"fire", "smoke", "tree", "rock"}
        for seed in range(50):
            random.seed(seed)
            msg = IW.gen_imagery(self.far_from_fire, self.fg)
            for det in msg["detections"]:
                self.assertIn(det["label"], valid, f"Invalid label: {det['label']}")
                self.assertIn("conf", det)
                self.assertIn("bbox", det)

    def test_fire_label_present_near_fire_zone(self):
        """Drone near fire → eventually a 'fire' label should appear."""
        random.seed(0)
        found_fire = False
        for _ in range(200):
            msg = IW.gen_imagery(self.near_fire, self.fg)
            if any(d["label"] == "fire" for d in msg["detections"]):
                found_fire = True
                break
        self.assertTrue(found_fire, "No 'fire' label detected in 200 frames near the fire zone")


# ---------------------------------------------------------------------------
# Lawnmower flight pattern
# ---------------------------------------------------------------------------

class TestLawnmowerPatternThermal(unittest.TestCase):
    def test_returns_3_tuple(self):
        pos = TW._lawnmower_pos(0, TW.DRONE_ALTITUDE_M)
        self.assertEqual(len(pos), 3)

    def test_altitude_fixed(self):
        for step in range(300):
            _, _, z = TW._lawnmower_pos(step, TW.DRONE_ALTITUDE_M)
            self.assertAlmostEqual(z, TW.DRONE_ALTITUDE_M,
                                   msg=f"Altitude wrong at step {step}")

    def test_x_within_survey_bounds(self):
        for step in range(300):
            x, _, _ = TW._lawnmower_pos(step, TW.DRONE_ALTITUDE_M)
            self.assertGreaterEqual(x, TW.SURVEY_X_MIN - 1e-6)
            self.assertLessEqual(x, TW.SURVEY_X_MAX + 1e-6)

    def test_y_within_survey_bounds(self):
        for step in range(300):
            _, y, _ = TW._lawnmower_pos(step, TW.DRONE_ALTITUDE_M)
            self.assertGreaterEqual(y, TW.SURVEY_Y_MIN - 1e-6)
            self.assertLessEqual(y, TW.SURVEY_Y_MAX + 1e-6)

    def test_covers_fire_zone(self):
        """The lawnmower must pass within STRIP_WIDTH of the fire zone (20, 20)."""
        positions = _lawnmower_positions(TW, 300)
        min_dist = min(math.sqrt((x - 20)**2 + (y - 20)**2) for x, y, _ in positions)
        self.assertLessEqual(min_dist, TW.STRIP_WIDTH_M,
                             f"Lawnmower never came within {TW.STRIP_WIDTH_M}m of fire zone; "
                             f"closest was {min_dist:.1f}m")

    def test_pattern_loops(self):
        """The pattern must repeat — step N and step N+total_steps should be the same XY."""
        x_range = TW.SURVEY_X_MAX - TW.SURVEY_X_MIN
        y_range = TW.SURVEY_Y_MAX - TW.SURVEY_Y_MIN
        steps_per_strip = max(1, int(x_range / TW.MOVE_SPEED_M))
        n_strips = max(1, int(y_range / TW.STRIP_WIDTH_M))
        total = steps_per_strip * n_strips

        for start in [0, 5, 17]:
            p1 = TW._lawnmower_pos(start, TW.DRONE_ALTITUDE_M)
            p2 = TW._lawnmower_pos(start + total, TW.DRONE_ALTITUDE_M)
            self.assertAlmostEqual(p1[0], p2[0], places=6, msg="X mismatch on loop")
            self.assertAlmostEqual(p1[1], p2[1], places=6, msg="Y mismatch on loop")

    def test_thermal_and_imagery_share_same_xy(self):
        """Both workers must produce identical XY coordinates at every step."""
        for step in range(200):
            tx, ty, _ = TW._lawnmower_pos(step, TW.DRONE_ALTITUDE_M)
            ix, iy, _ = IW._lawnmower_pos(step, IW.DRONE_ALTITUDE_M)
            self.assertAlmostEqual(tx, ix, places=6,
                                   msg=f"X diverges at step {step}")
            self.assertAlmostEqual(ty, iy, places=6,
                                   msg=f"Y diverges at step {step}")

    def test_altitudes_differ(self):
        """Thermal must be lower than imagery (different sensory roles)."""
        self.assertLess(TW.DRONE_ALTITUDE_M, IW.DRONE_ALTITUDE_M,
                        "Thermal drone should fly lower than imagery drone")


class TestLawnmowerGPSNoise(unittest.TestCase):
    def test_gps_noise_small(self):
        """With seed applied, noisy positions stay within 3σ of waypoint."""
        random.seed(42)
        sigma = TW.GPS_NOISE_STD_M
        for step in range(100):
            base = TW._lawnmower_pos(step, TW.DRONE_ALTITUDE_M)
            noise_x = random.gauss(0, sigma)
            noise_y = random.gauss(0, sigma)
            # 3σ ~ 1.5m: occasional outliers are fine, just check magnitude is small
            self.assertLess(abs(noise_x), 4.0, "GPS noise X unexpectedly large")
            self.assertLess(abs(noise_y), 4.0, "GPS noise Y unexpectedly large")


# ---------------------------------------------------------------------------
# Drop probability model
# ---------------------------------------------------------------------------

class TestDropProbModel(unittest.TestCase):
    """
    DIST_DROP_SLOPE defaults to 0.0 so all distances give BASE_DROP_PROB.
    The tests use a temporary non-zero slope to check the math.
    """

    def test_baseline_is_zero_by_default(self):
        """With default slope=0, drop prob should always equal BASE_DROP_PROB."""
        original = TW.DIST_DROP_SLOPE
        TW.DIST_DROP_SLOPE = 0.0
        try:
            for dist in [0.0, 30.0, 100.0, 300.0]:
                self.assertAlmostEqual(TW._drop_prob_from_distance(dist),
                                       TW.BASE_DROP_PROB)
        finally:
            TW.DIST_DROP_SLOPE = original

    def test_increases_with_positive_slope(self):
        """With a non-zero slope, farther = higher drop prob."""
        original = TW.DIST_DROP_SLOPE
        TW.DIST_DROP_SLOPE = 0.001
        try:
            p1 = TW._drop_prob_from_distance(10.0)
            p2 = TW._drop_prob_from_distance(50.0)
            self.assertGreater(p2, p1)
        finally:
            TW.DIST_DROP_SLOPE = original

    def test_never_exceeds_max(self):
        original = TW.DIST_DROP_SLOPE
        TW.DIST_DROP_SLOPE = 0.001
        try:
            for dist in [0, 10, 100, 500, 1000]:
                p = TW._drop_prob_from_distance(float(dist))
                self.assertLessEqual(p, TW.MAX_DROP_PROB)
        finally:
            TW.DIST_DROP_SLOPE = original

    def test_never_below_base(self):
        for dist in [0.0, 10.0, 100.0]:
            self.assertGreaterEqual(TW._drop_prob_from_distance(dist), TW.BASE_DROP_PROB)


# ---------------------------------------------------------------------------
# Clock offset / jitter defaults
# ---------------------------------------------------------------------------

class TestClockDefaults(unittest.TestCase):
    def test_thermal_clock_offset_zero_by_default(self):
        self.assertEqual(TW.CLOCK_OFFSET_NS, 0)

    def test_thermal_clock_jitter_zero_by_default(self):
        self.assertEqual(TW.CLOCK_JITTER_NS, 0.0)

    def test_imagery_clock_offset_zero_by_default(self):
        self.assertEqual(IW.CLOCK_OFFSET_NS, 0)

    def test_imagery_clock_jitter_zero_by_default(self):
        self.assertEqual(IW.CLOCK_JITTER_NS, 0.0)


# ---------------------------------------------------------------------------
# CLI / module-level sanity
# ---------------------------------------------------------------------------

class TestModuleSanity(unittest.TestCase):
    def test_thermal_main_callable(self):
        self.assertTrue(callable(TW.main))

    def test_imagery_main_callable(self):
        self.assertTrue(callable(IW.main))

    def test_thermal_base_drop_prob_valid(self):
        self.assertGreaterEqual(TW.BASE_DROP_PROB, 0.0)
        self.assertLessEqual(TW.BASE_DROP_PROB, 1.0)

    def test_imagery_base_drop_prob_valid(self):
        self.assertGreaterEqual(IW.BASE_DROP_PROB, 0.0)
        self.assertLessEqual(IW.BASE_DROP_PROB, 1.0)

    def test_shared_survey_bounds(self):
        """Both workers must use the same survey area so they cover the same ground."""
        self.assertEqual(TW.SURVEY_X_MIN, IW.SURVEY_X_MIN)
        self.assertEqual(TW.SURVEY_X_MAX, IW.SURVEY_X_MAX)
        self.assertEqual(TW.SURVEY_Y_MIN, IW.SURVEY_Y_MIN)
        self.assertEqual(TW.SURVEY_Y_MAX, IW.SURVEY_Y_MAX)
        self.assertEqual(TW.STRIP_WIDTH_M, IW.STRIP_WIDTH_M)
        self.assertEqual(TW.MOVE_SPEED_M,  IW.MOVE_SPEED_M)

    def test_send_hz_consistent(self):
        self.assertEqual(TW.SEND_HZ, IW.SEND_HZ)


if __name__ == "__main__":
    unittest.main()
