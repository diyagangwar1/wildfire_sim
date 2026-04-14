"""
Syntax / compile / import checks for every project file.

Run:  python3 -m unittest tests/test_syntax.py -v
"""

import importlib
import os
import py_compile
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CORE_FILES = [
    "controller.py",
    "thermal_worker.py",
    "imagery_worker.py",
    "gps_time.py",
    "fire_model.py",
    "analyze_latency.py",
    "mn_topo.py",
    "run_experiments.py",
    "compare_seeds.py",
]


class TestSyntax(unittest.TestCase):
    """py_compile every source file — instant guard against SyntaxErrors."""

    def _compile(self, filename: str) -> None:
        path = os.path.join(REPO_ROOT, filename)
        self.assertTrue(os.path.exists(path), f"{filename} not found in repo root")
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            self.fail(f"{filename} failed to compile:\n{exc}")

    def test_controller(self):        self._compile("controller.py")
    def test_thermal_worker(self):    self._compile("thermal_worker.py")
    def test_imagery_worker(self):    self._compile("imagery_worker.py")
    def test_gps_time(self):          self._compile("gps_time.py")
    def test_fire_model(self):        self._compile("fire_model.py")
    def test_analyze_latency(self):   self._compile("analyze_latency.py")
    def test_mn_topo(self):           self._compile("mn_topo.py")
    def test_run_experiments(self):   self._compile("run_experiments.py")
    def test_compare_seeds(self):     self._compile("compare_seeds.py")


class TestImports(unittest.TestCase):
    """
    Actually import each module so Python resolves all top-level names.
    Catches missing imports, bad module-level code, and renamed functions.
    """

    def test_import_gps_time(self):
        mod = importlib.import_module("gps_time")
        self.assertTrue(callable(getattr(mod, "utc_ns", None)))
        self.assertTrue(callable(getattr(mod, "sleep_to_next_tick", None)))

    def test_import_fire_model(self):
        mod = importlib.import_module("fire_model")
        self.assertTrue(callable(getattr(mod, "FireGrid", None)),
                        "fire_model.FireGrid class is missing")

    def test_import_thermal_worker(self):
        mod = importlib.import_module("thermal_worker")
        self.assertTrue(callable(getattr(mod, "gen_thermal", None)),
                        "thermal_worker.gen_thermal is missing")
        self.assertTrue(callable(getattr(mod, "_lawnmower_pos", None)),
                        "thermal_worker._lawnmower_pos is missing (random walk removed)")
        self.assertTrue(callable(getattr(mod, "_drop_prob_from_distance", None)))

    def test_import_imagery_worker(self):
        mod = importlib.import_module("imagery_worker")
        self.assertTrue(callable(getattr(mod, "gen_imagery", None)),
                        "imagery_worker.gen_imagery is missing")
        self.assertTrue(callable(getattr(mod, "_lawnmower_pos", None)),
                        "imagery_worker._lawnmower_pos is missing")

    def test_import_controller(self):
        mod = importlib.import_module("controller")
        self.assertTrue(callable(getattr(mod, "try_evaluate", None)))
        self.assertTrue(callable(getattr(mod, "safe_max_temp", None)))
        self.assertTrue(callable(getattr(mod, "imagery_has_fire", None)))

    def test_run_experiments_has_experiments_list(self):
        """EXPERIMENTS must be a list of dicts (not tuples) after the refactor."""
        mod = importlib.import_module("run_experiments")
        exps = getattr(mod, "EXPERIMENTS", None)
        self.assertIsNotNone(exps, "run_experiments.EXPERIMENTS missing")
        self.assertIsInstance(exps, list)
        self.assertGreater(len(exps), 0)
        first = exps[0]
        self.assertIsInstance(first, dict,
                              "EXPERIMENTS entries should be dicts (not tuples)")
        for required_key in ("name", "group", "thermal_delay", "imagery_delay",
                             "thermal_loss", "imagery_loss",
                             "thermal_clock_offset_ms", "imagery_clock_offset_ms",
                             "dist_drop_slope", "fire_seed"):
            self.assertIn(required_key, first,
                          f"EXPERIMENTS entry missing key '{required_key}'")


if __name__ == "__main__":
    unittest.main()
