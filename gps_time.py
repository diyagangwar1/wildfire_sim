"""
GPS/UTC Time Utilities

In a real drone, GPS time comes from a receiver (PPS + NMEA/UBX, etc.).
For this Mininet/VM testbed we use UTC epoch nanoseconds from time.time_ns().
On real hardware, replace utc_ns() with actual GPS reads — format stays the same.

Also exposes:
  sleep_to_next_tick(period)   GPS-snap send schedule so workers align to the
                                same clock grid → tx_ns values within ~ms.
  is_fire_window()             Deterministic fire-active phase derived from
                                time-since-process-start (not wall clock) so
                                every experiment begins at the same fire-cycle
                                phase, making detection rates comparable across
                                runs with different seeds and delay settings.
"""

from __future__ import annotations
import math
import time
from datetime import datetime, timezone


def utc_ns() -> int:
    """UTC epoch timestamp in nanoseconds."""
    return time.time_ns()


def utc_iso(ts_ns: int | None = None) -> str:
    """Human-readable ISO-8601 UTC timestamp (for debug/logging)."""
    if ts_ns is None:
        ts_ns = utc_ns()
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sleep_to_next_tick(period: float) -> None:
    """
    Sleep until the next period-aligned wall-clock tick.

    Both workers calling this with the same period will wake at the same
    instants (e.g. every 0.5 s: t=0.0, 0.5, 1.0 ...), so their tx_ns
    values are within a few milliseconds of each other.  This makes the
    GPS sync threshold in the controller (SYNC_THRESHOLD_MS) meaningful.

    On real GPS-synchronized drones the equivalent is a PPS-triggered send.
    """
    now = time.time()
    next_tick = math.ceil(now / period) * period
    wait = next_tick - now
    if wait < 0.005:        # avoid a near-zero sleep — jump to the next tick
        wait += period
    time.sleep(wait)


# ---------------------------------------------------------------------------
# Shared fire-event schedule
# ---------------------------------------------------------------------------
FIRE_CYCLE_S: float = 8.0   # seconds per fire cycle
FIRE_ON_S: float = 3.0      # fire is "active" for this many seconds per cycle
                             # → ~37.5 % duty cycle

# Module-load time used as the zero-point for the fire schedule.
# Using time-since-start rather than absolute wall clock ensures every
# experiment begins at the same phase of the fire cycle (t=0), regardless
# of when in the day the experiment is launched.  Without this, each
# experiment inherits a different wall-clock offset, causing systematic
# variation in observed fire rates that masks the real delay effect.
_PROCESS_START_S: float = time.time()


def is_fire_window(cycle_s: float = FIRE_CYCLE_S, on_s: float = FIRE_ON_S) -> bool:
    """
    Returns True when time-since-process-start falls inside a 'fire active' window.

    Both workers start within ~1 second of each other per experiment, so
    they share the same fire-window phase.  Every experiment starts at
    t≈0 in the cycle, giving a consistent fire schedule across all runs.

    Timeline example (cycle=8s, on=3s):
      t=0–3s   : fire ON  (both sensors should detect fire)
      t=3–8s   : fire OFF (both sensors should see baseline)
      t=8–11s  : fire ON  ...
    """
    t = time.time() - _PROCESS_START_S   # seconds since this process started
    return (t % cycle_s) < on_s
