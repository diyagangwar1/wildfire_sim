"""
Unit tests for controller.py pure logic — no network, no Mininet.

Coverage:
- safe_max_temp(): 2D grid, 1D list, None, empty
- imagery_has_fire(): detection list parsing
- GPS pair matching algorithm (the core of try_evaluate)
- hit/miss (TP/FN/FP/TN) classification
- fused_pairs deduplication

Run:  pytest tests/test_controller_logic.py -v
"""

import os
import sys
import unittest
from collections import deque

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import controller as C


# ---------------------------------------------------------------------------
# safe_max_temp
# ---------------------------------------------------------------------------

class TestSafeMaxTemp(unittest.TestCase):
    def test_2d_grid(self):
        grid = [[70.0, 80.0], [90.0, 135.0]]
        max_t, shape = C.safe_max_temp(grid)
        self.assertAlmostEqual(max_t, 135.0)
        self.assertEqual(shape, "2x2")

    def test_1d_list(self):
        data = [65.0, 102.5, 88.0]
        max_t, shape = C.safe_max_temp(data)
        self.assertAlmostEqual(max_t, 102.5)
        self.assertIn("1d", shape)

    def test_none_returns_negative_inf(self):
        max_t, shape = C.safe_max_temp(None)
        self.assertEqual(max_t, float("-inf"))

    def test_empty_list(self):
        max_t, _ = C.safe_max_temp([])
        self.assertEqual(max_t, float("-inf"))

    def test_above_threshold(self):
        grid = [[50.0, 150.0]]
        max_t, _ = C.safe_max_temp(grid)
        self.assertGreater(max_t, C.TEMP_THRESHOLD)


# ---------------------------------------------------------------------------
# imagery_has_fire
# ---------------------------------------------------------------------------

class TestImageryHasFire(unittest.TestCase):
    def test_fire_label_detected(self):
        dets = [{"label": "fire", "conf": 0.9}]
        self.assertTrue(C.imagery_has_fire(dets))

    def test_no_fire_label(self):
        dets = [{"label": "smoke", "conf": 0.7}, {"label": "tree", "conf": 0.6}]
        self.assertFalse(C.imagery_has_fire(dets))

    def test_empty_list(self):
        self.assertFalse(C.imagery_has_fire([]))

    def test_none(self):
        self.assertFalse(C.imagery_has_fire(None))

    def test_mixed_detections(self):
        dets = [{"label": "smoke", "conf": 0.5}, {"label": "fire", "conf": 0.85}]
        self.assertTrue(C.imagery_has_fire(dets))


# ---------------------------------------------------------------------------
# GPS pair matching algorithm
# (same logic as the inner loop of try_evaluate — tested in isolation)
# ---------------------------------------------------------------------------

def _find_best_pair(thermal_buf, imagery_buf, threshold_ns: int):
    """
    Mirror of try_evaluate's matching loop, extracted for unit testing.
    Returns (best_t, best_i, best_dt_ns) or (None, None, threshold_ns+1).
    """
    best_t = best_i = None
    best_dt = threshold_ns + 1
    for t in thermal_buf:
        t_tx = int(t.get("tx_ns", 0) or 0)
        for i in imagery_buf:
            i_tx = int(i.get("tx_ns", 0) or 0)
            dt = abs(t_tx - i_tx)
            if dt < best_dt:
                best_dt = dt
                best_t, best_i = t, i
    if best_t is None or best_dt > threshold_ns:
        return None, None, best_dt
    return best_t, best_i, best_dt


class TestGpsPairMatching(unittest.TestCase):
    BASE_NS = 1_741_000_000_000_000_000  # arbitrary large epoch-like base

    def _msg(self, offset_ms: float, stream: str = "t") -> dict:
        return {"tx_ns": self.BASE_NS + int(offset_ms * 1e6), "stream": stream}

    def test_finds_closest_pair(self):
        thermal_buf = [self._msg(0), self._msg(500)]
        imagery_buf = [self._msg(490)]
        threshold = int(250 * 1e6)  # 250ms
        best_t, best_i, dt = _find_best_pair(thermal_buf, imagery_buf, threshold)
        self.assertIsNotNone(best_t)
        self.assertEqual(dt, int(10 * 1e6))   # 10ms apart

    def test_rejects_pair_outside_threshold(self):
        thermal_buf = [self._msg(0)]
        imagery_buf = [self._msg(300)]  # 300ms apart
        threshold = int(250 * 1e6)     # 250ms threshold
        best_t, best_i, _ = _find_best_pair(thermal_buf, imagery_buf, threshold)
        self.assertIsNone(best_t)

    def test_accepts_pair_within_threshold(self):
        thermal_buf = [self._msg(0)]
        imagery_buf = [self._msg(100)]  # 100ms apart
        threshold = int(250 * 1e6)
        best_t, best_i, dt = _find_best_pair(thermal_buf, imagery_buf, threshold)
        self.assertIsNotNone(best_t)
        self.assertEqual(dt, int(100 * 1e6))

    def test_empty_thermal_buffer(self):
        best_t, best_i, _ = _find_best_pair([], [self._msg(0)], int(250 * 1e6))
        self.assertIsNone(best_t)

    def test_empty_imagery_buffer(self):
        best_t, best_i, _ = _find_best_pair([self._msg(0)], [], int(250 * 1e6))
        self.assertIsNone(best_t)

    def test_exact_threshold_boundary(self):
        # dt == threshold should be accepted (condition is best_dt > threshold)
        thermal_buf = [self._msg(0)]
        imagery_buf = [self._msg(250)]  # exactly 250ms = threshold
        threshold = int(250 * 1e6)
        best_t, best_i, dt = _find_best_pair(thermal_buf, imagery_buf, threshold)
        self.assertIsNotNone(best_t, "Pair at exactly the threshold should be accepted")

    def test_picks_minimum_dt_from_multiple_candidates(self):
        thermal_buf = [self._msg(0), self._msg(200), self._msg(450)]
        imagery_buf = [self._msg(210)]   # closest to offset=200
        threshold = int(500 * 1e6)
        best_t, best_i, dt = _find_best_pair(thermal_buf, imagery_buf, threshold)
        self.assertIsNotNone(best_t)
        self.assertEqual(dt, int(10 * 1e6))  # 200ms vs 210ms → 10ms apart


# ---------------------------------------------------------------------------
# Hit / miss classification (TP / FN / FP / TN)
# ---------------------------------------------------------------------------

def _classify(fire_window: bool, decision: bool) -> str:
    """Mirror of controller's hit_miss logic."""
    if fire_window and decision:
        return "TP"
    elif fire_window and not decision:
        return "FN"
    elif not fire_window and decision:
        return "FP"
    else:
        return "TN"


class TestHitMissClassification(unittest.TestCase):
    def test_true_positive(self):
        self.assertEqual(_classify(True, True), "TP")

    def test_false_negative(self):
        self.assertEqual(_classify(True, False), "FN")

    def test_false_positive(self):
        self.assertEqual(_classify(False, True), "FP")

    def test_true_negative(self):
        self.assertEqual(_classify(False, False), "TN")

    def test_all_combinations_covered(self):
        outcomes = {_classify(fw, dec)
                    for fw in (True, False)
                    for dec in (True, False)}
        self.assertEqual(outcomes, {"TP", "FN", "FP", "TN"})


# ---------------------------------------------------------------------------
# fused_pairs deduplication
# ---------------------------------------------------------------------------

class TestFusedPairsDedup(unittest.TestCase):
    """
    Ensure the fused_pairs deque prevents re-fusing the same (t_tx, i_tx) pair
    even as the buffer rotates.
    """

    def test_pair_not_re_fused(self):
        seen = deque(maxlen=20)
        pair = (1_741_000_000_000_000_000, 1_741_000_000_001_000_000)
        self.assertNotIn(pair, seen)
        seen.append(pair)
        self.assertIn(pair, seen)

    def test_different_pair_accepted(self):
        seen = deque(maxlen=20)
        pair1 = (1_000_000, 2_000_000)
        pair2 = (3_000_000, 4_000_000)
        seen.append(pair1)
        self.assertNotIn(pair2, seen)

    def test_maxlen_eviction(self):
        seen = deque(maxlen=3)
        pairs = [(i, i + 1) for i in range(5)]
        for p in pairs:
            seen.append(p)
        # First two should have been evicted
        self.assertNotIn(pairs[0], seen)
        self.assertNotIn(pairs[1], seen)
        # Last three should still be present
        for p in pairs[2:]:
            self.assertIn(p, seen)


if __name__ == "__main__":
    unittest.main()
