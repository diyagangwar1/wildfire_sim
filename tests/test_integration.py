"""
Integration test — no Mininet required.

Starts controller + both workers as plain subprocesses connecting to
localhost. Verifies that fusions are actually recorded within 20 seconds.
This test catches:
  - Import / runtime errors in any module
  - Controller failing to bind ports
  - Workers failing to connect or send messages
  - Fusion logic producing zero output

Run:  pytest tests/test_integration.py -v   (takes ~20 seconds)
Skip: pytest tests/ -v -m "not integration"
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SECS = 20        # how long to let the system run
MIN_FUSIONS = 5      # minimum fusions expected in RUN_SECS seconds

# Skip by default when running plain `python3 -m unittest` (takes ~20s).
# Enable with:  RUN_INTEGRATION=1 python3 -m unittest ...
#           or: pytest tests/ -v   (pytest always runs it)
_RUN = os.environ.get("RUN_INTEGRATION", "0") not in ("", "0", "false", "no")


def _start_proc(cmd, logpath):
    log = open(logpath, "w")
    return subprocess.Popen(cmd, stdout=log, stderr=log), log


def _tail(path, n=30):
    try:
        lines = open(path).readlines()
        return "".join(lines[-n:])
    except Exception:
        return "(could not read log)"


@unittest.skipUnless(_RUN, "Set RUN_INTEGRATION=1 to run (takes ~20s, no Mininet needed)")
class TestEndToEndNoMininet(unittest.TestCase):
    """
    Full pipeline test without Mininet — controller and workers connect
    over localhost TCP.  Verifies the complete message flow:
    thermal → controller ← imagery → fusion → latency_log.jsonl
    """

    def test_fusions_recorded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            python = sys.executable
            ctrl_log  = os.path.join(tmpdir, "ctrl.log")
            th_log    = os.path.join(tmpdir, "thermal.log")
            im_log    = os.path.join(tmpdir, "imagery.log")

            # Start controller — large sync threshold so localhost timing is fine
            ctrl_proc, ctrl_f = _start_proc(
                [python, os.path.join(REPO_ROOT, "controller.py"),
                 "--outdir", tmpdir,
                 "--sync-threshold-ms", "2000"],
                ctrl_log,
            )

            time.sleep(1.5)  # let controller bind ports 5001 / 5002

            # Start workers connecting to localhost
            th_proc, th_f = _start_proc(
                [python, os.path.join(REPO_ROOT, "thermal_worker.py"),
                 "127.0.0.1",
                 "--seed", "42",
                 "--base-drop-prob", "0"],
                th_log,
            )
            im_proc, im_f = _start_proc(
                [python, os.path.join(REPO_ROOT, "imagery_worker.py"),
                 "127.0.0.1",
                 "--seed", "42",
                 "--base-drop-prob", "0"],
                im_log,
            )

            time.sleep(RUN_SECS)

            # Graceful shutdown
            for proc in [th_proc, im_proc, ctrl_proc]:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except Exception:
                    proc.kill()
            for f in [ctrl_f, th_f, im_f]:
                f.close()

            latency_log = os.path.join(tmpdir, "latency_log.jsonl")

            # ── Assert 1: file exists ────────────────────────────────────────
            self.assertTrue(
                os.path.exists(latency_log),
                f"latency_log.jsonl was not created.\n"
                f"controller.log:\n{_tail(ctrl_log)}\n"
                f"thermal.log:\n{_tail(th_log)}"
            )

            # ── Assert 2: file has fusions ───────────────────────────────────
            with open(latency_log) as f:
                lines = [l.strip() for l in f if l.strip()]

            self.assertGreaterEqual(
                len(lines), MIN_FUSIONS,
                f"Expected >= {MIN_FUSIONS} fusions in {RUN_SECS}s, got {len(lines)}.\n"
                f"controller.log:\n{_tail(ctrl_log)}\n"
                f"thermal.log:\n{_tail(th_log)}\n"
                f"imagery.log:\n{_tail(im_log)}"
            )

            # ── Assert 3: record schema is correct ───────────────────────────
            rec = json.loads(lines[0])
            required_fields = [
                "fusion_id", "e2e_ms", "raw_signal", "decision",
                "fire_window", "hit_miss",
            ]
            for field in required_fields:
                self.assertIn(
                    field, rec,
                    f"Field '{field}' missing from fusion record.\n"
                    f"Got keys: {list(rec.keys())}"
                )

            # ── Assert 4: hit_miss is a valid label ──────────────────────────
            valid_labels = {"TP", "FP", "FN", "TN"}
            for line in lines[:10]:
                r = json.loads(line)
                self.assertIn(
                    r.get("hit_miss"), valid_labels,
                    f"Invalid hit_miss value: {r.get('hit_miss')}"
                )

            # ── Assert 5: e2e_ms is a positive number ────────────────────────
            for line in lines[:10]:
                r = json.loads(line)
                self.assertGreater(
                    float(r["e2e_ms"]), 0,
                    f"e2e_ms should be > 0, got {r['e2e_ms']}"
                )

            print(f"\n  [integration] {len(lines)} fusions in {RUN_SECS}s — all assertions passed.")


if __name__ == "__main__":
    unittest.main()
