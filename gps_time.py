"""
GPS/UTC Time Utilities

In a real drone, GPS time comes from a receiver (PPS + NMEA/UBX, etc.).
For this Mininet/VM testbed we use UTC epoch nanoseconds from time.time_ns().
On real hardware, replace utc_ns() with actual GPS reads — format stays the same.

Also exposes:
  sleep_to_next_tick(period)   GPS-snap send schedule so workers align to the
                                same clock grid → tx_ns values within ~ms.
  is_fire_window()             Deterministic fire-active phase derived from wall
                                clock — both workers call this independently and
                                agree on "fire on / fire off" without any
                                coordination.  Fixes the independent-fire-model
                                problem that made raw detection rate ~11%.
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

def is_fire_window(cycle_s: float = FIRE_CYCLE_S, on_s: float = FIRE_ON_S) -> bool:
    """
    Returns True when the current GPS time falls inside a 'fire active' window.

    Both workers call this independently and agree without any coordination,
    because the window is a deterministic function of wall-clock time.
    This correlates thermal and imagery fire signals so that detection rate
    reflects real fire events rather than random coincidence.

    Timeline example (cycle=8s, on=3s):
      t=0–3s   : fire ON  (both sensors should detect fire)
      t=3–8s   : fire OFF (both sensors should see baseline)
      t=8–11s  : fire ON  ...
    """
    t = utc_ns() / 1e9          # seconds since epoch
    return (t % cycle_s) < on_s
