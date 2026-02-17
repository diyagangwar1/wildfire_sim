"""
GPS/UTC Time Utilities (Phase 3)

Your professor wants GPS/UTC time, not "plain Python time".
In a real drone, you'd read GPS time from a GPS receiver (PPS + NMEA/UBX, etc.).

For this Mininet/VM testbed, we use UTC epoch timestamps in nanoseconds from
time.time_ns(), which is a UTC-based wall clock.

IMPORTANT NOTE:
- This is "UTC time" and is the right *format* + *global clock assumption* for Phase 3.
- On real hardware, replace utc_ns() with actual GPS time reads (still output ns).
"""

from __future__ import annotations
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
    # Keep milliseconds for readability; ns stays in the numeric field.
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
