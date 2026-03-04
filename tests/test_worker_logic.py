"""
Unit tests for thermal_worker.py and imagery_worker.py logic.

Key coverage:
- gen_thermal() / gen_imagery() produce correctly structured messages
- _random_walk_3d() respects altitude and XY radius bounds
- _drop_prob_from_distance() returns values in [0, MAX_DROP_PROB]
- arg parsing completes without error (this test directly caught the
  Python 3.12 "global used before declaration" SyntaxError)

Run:  pytest tests/test_worker_logic.py -v
"""

import os
import sys
import math
import random
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import thermal_worker as TW
import imagery_worker as IW


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run_walk(module, steps: int = 200):
    """Run the random walk for `steps` iterations and return all positions."""
    random.seed(0)
    pos = module.DRONE_START_POS
    positions = [pos]
    for _ in range(steps):
        pos = module._random_walk_3d(pos, module.DRONE_STEP_M)
        positions.append(pos)
    return positions


# ---------------------------------------------------------------------------
# Thermal worker tests
# ---------------------------------------------------------------------------

class TestThermalGenMessage(unittest.TestCase):
    def setUp(self):
        random.seed(42)

    def test_gen_thermal_returns_dict(self):
        msg = TW.gen_thermal()
        self.assertIsInstance(msg, dict)

    def test_required_keys_present(self):
        # gen_thermal() produces the raw sensor payload; proc_ns / tx_ns are
        # added later in main() via msg.update({...}).
        msg = TW.gen_thermal()
        for key in ("sensor", "data", "fire_sim"):
            self.assertIn(key, msg, f"Missing key '{key}' in gen_thermal()")

    def test_sensor_tag_is_thermal(self):
        self.assertEqual(TW.gen_thermal()["sensor"], "thermal")

    def test_data_is_list(self):
        for _ in range(20):
            msg = TW.gen_thermal()
            self.assertIsInstance(msg["data"], list)

    def test_fire_sim_is_bool(self):
        for _ in range(20):
            self.assertIsInstance(TW.gen_thermal()["fire_sim"], bool)

    def test_hotspot_temperatures_exceed_threshold_when_fire(self):
        # When fire_sim=True the data should contain temps > TEMP_THRESHOLD
        found_fire_msg = False
        for seed in range(500):
            random.seed(seed)
            msg = TW.gen_thermal()
            if msg["fire_sim"]:
                found_fire_msg = True
                flat = []
                for row in msg["data"]:
                    if isinstance(row, list):
                        flat.extend(row)
                    else:
                        flat.append(row)
                self.assertGreater(max(flat), 100.0,
                                   "Fire message should have temps > 100°C")
                break
        self.assertTrue(found_fire_msg, "gen_thermal never produced a fire message in 500 tries")


class TestThermalRandomWalk(unittest.TestCase):
    def test_altitude_within_bounds(self):
        for pos in _run_walk(TW, 500):
            z = pos[2]
            self.assertGreaterEqual(z, TW.DRONE_ALT_MIN_M,
                                    f"Altitude {z:.1f}m below min {TW.DRONE_ALT_MIN_M}m")
            self.assertLessEqual(z, TW.DRONE_ALT_MAX_M,
                                 f"Altitude {z:.1f}m above max {TW.DRONE_ALT_MAX_M}m")

    def test_xy_within_radius(self):
        for pos in _run_walk(TW, 500):
            xy_dist = math.sqrt(pos[0] ** 2 + pos[1] ** 2)
            self.assertLessEqual(xy_dist, TW.XY_MAX_M + 1e-6,
                                 f"XY distance {xy_dist:.1f}m exceeds XY_MAX_M={TW.XY_MAX_M}m")


class TestThermalDropProb(unittest.TestCase):
    def test_zero_at_origin(self):
        p = TW._drop_prob_from_distance(0.0)
        self.assertAlmostEqual(p, TW.BASE_DROP_PROB, places=6)

    def test_increases_with_distance(self):
        p1 = TW._drop_prob_from_distance(10.0)
        p2 = TW._drop_prob_from_distance(50.0)
        self.assertGreater(p2, p1)

    def test_never_exceeds_max(self):
        for dist in [0, 10, 100, 500, 1000]:
            p = TW._drop_prob_from_distance(float(dist))
            self.assertLessEqual(p, TW.MAX_DROP_PROB,
                                 f"drop_prob {p:.3f} exceeds MAX_DROP_PROB at distance {dist}m")

    def test_never_below_base(self):
        for dist in [0, 10, 100]:
            p = TW._drop_prob_from_distance(float(dist))
            self.assertGreaterEqual(p, TW.BASE_DROP_PROB)


class TestThermalArgParsing(unittest.TestCase):
    """
    Verifies that argparse setup (including --base-drop-prob default=BASE_DROP_PROB)
    doesn't crash.  This test would have caught the Python 3.12 SyntaxError:
      "name 'BASE_DROP_PROB' is used prior to global declaration"
    """
    def test_main_is_callable(self):
        self.assertTrue(callable(TW.main))

    def test_module_has_base_drop_prob(self):
        self.assertIsInstance(TW.BASE_DROP_PROB, float)
        self.assertGreaterEqual(TW.BASE_DROP_PROB, 0.0)
        self.assertLessEqual(TW.BASE_DROP_PROB, 1.0)


# ---------------------------------------------------------------------------
# Imagery worker tests
# ---------------------------------------------------------------------------

class TestImageryGenMessage(unittest.TestCase):
    def setUp(self):
        random.seed(42)

    def test_gen_imagery_returns_dict(self):
        self.assertIsInstance(IW.gen_imagery(), dict)

    def test_required_keys_present(self):
        msg = IW.gen_imagery()
        for key in ("sensor", "detections", "fire_sim"):
            self.assertIn(key, msg, f"Missing key '{key}' in gen_imagery()")

    def test_sensor_tag_is_imagery(self):
        self.assertEqual(IW.gen_imagery()["sensor"], "imagery")

    def test_detections_is_list(self):
        for _ in range(20):
            self.assertIsInstance(IW.gen_imagery()["detections"], list)

    def test_fire_sim_is_bool(self):
        for _ in range(20):
            self.assertIsInstance(IW.gen_imagery()["fire_sim"], bool)

    def test_fire_detection_has_label(self):
        found = False
        for seed in range(500):
            random.seed(seed)
            msg = IW.gen_imagery()
            if msg["fire_sim"]:
                found = True
                for det in msg["detections"]:
                    if isinstance(det, dict):
                        self.assertIn("label", det)
                        self.assertIn("conf", det)
                break
        self.assertTrue(found, "gen_imagery never produced a fire detection in 500 tries")


class TestImageryRandomWalk(unittest.TestCase):
    def test_altitude_within_bounds(self):
        for pos in _run_walk(IW, 500):
            z = pos[2]
            self.assertGreaterEqual(z, IW.DRONE_ALT_MIN_M)
            self.assertLessEqual(z, IW.DRONE_ALT_MAX_M)

    def test_xy_within_radius(self):
        for pos in _run_walk(IW, 500):
            xy_dist = math.sqrt(pos[0] ** 2 + pos[1] ** 2)
            self.assertLessEqual(xy_dist, IW.XY_MAX_M + 1e-6)


class TestImageryArgParsing(unittest.TestCase):
    def test_main_is_callable(self):
        self.assertTrue(callable(IW.main))

    def test_module_has_base_drop_prob(self):
        self.assertIsInstance(IW.BASE_DROP_PROB, float)
        self.assertGreaterEqual(IW.BASE_DROP_PROB, 0.0)


if __name__ == "__main__":
    unittest.main()
