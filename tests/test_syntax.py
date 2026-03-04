"""
Syntax / compile check for every project file.

This test would have caught the Python 3.12 SyntaxError:
  "name 'BASE_DROP_PROB' is used prior to global declaration"
before any experiment was even attempted.

Run:  pytest tests/test_syntax.py -v
"""

import os
import py_compile
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CORE_FILES = [
    "controller.py",
    "thermal_worker.py",
    "imagery_worker.py",
    "gps_time.py",
    "analyze_latency.py",
    "mn_topo.py",
    "run_experiments.py",
    "compare_runs.py",
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

    def test_controller(self):         self._compile("controller.py")
    def test_thermal_worker(self):     self._compile("thermal_worker.py")
    def test_imagery_worker(self):     self._compile("imagery_worker.py")
    def test_gps_time(self):           self._compile("gps_time.py")
    def test_analyze_latency(self):    self._compile("analyze_latency.py")
    def test_mn_topo(self):            self._compile("mn_topo.py")
    def test_run_experiments(self):    self._compile("run_experiments.py")
    def test_compare_runs(self):       self._compile("compare_runs.py")


class TestImports(unittest.TestCase):
    """
    Actually import each worker module so Python resolves all top-level names.
    Catches issues like missing imports or bad module-level code.
    """

    def _add_root(self):
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)

    def test_import_gps_time(self):
        self._add_root()
        import importlib
        mod = importlib.import_module("gps_time")
        self.assertTrue(callable(getattr(mod, "utc_ns", None)))
        self.assertTrue(callable(getattr(mod, "is_fire_window", None)))
        self.assertTrue(callable(getattr(mod, "sleep_to_next_tick", None)))

    def test_import_thermal_worker(self):
        self._add_root()
        import importlib
        mod = importlib.import_module("thermal_worker")
        self.assertTrue(callable(getattr(mod, "gen_thermal", None)))
        self.assertTrue(callable(getattr(mod, "_random_walk_3d", None)))

    def test_import_imagery_worker(self):
        self._add_root()
        import importlib
        mod = importlib.import_module("imagery_worker")
        self.assertTrue(callable(getattr(mod, "gen_imagery", None)))

    def test_import_controller(self):
        self._add_root()
        import importlib
        mod = importlib.import_module("controller")
        self.assertTrue(callable(getattr(mod, "try_evaluate", None)))
        self.assertTrue(callable(getattr(mod, "safe_max_temp", None)))
        self.assertTrue(callable(getattr(mod, "imagery_has_fire", None)))


if __name__ == "__main__":
    unittest.main()
