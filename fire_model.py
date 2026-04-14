"""
Cellular Automaton Fire Propagation Model

Simulates a wildfire spreading on a 2D grid.  Both the thermal and imagery
workers import this module and maintain an identical FireGrid instance because:

  1. They both use the same `fire_seed` (default 0, independent of the
     trajectory seed so the fire pattern is the same across all Monte Carlo runs).
  2. They both advance the grid by calling `advance_to_wall_clock_step()` which
     computes the current step from absolute wall-clock time.  Since both workers
     are clock-synced (via sleep_to_next_tick), they always land on the same step.

This gives both sensors a consistent, spatially evolving ground truth without
any communication between them.

Fire dynamics
─────────────
  States : UNBURNED → BURNING → BURNED
  Each step (0.5 s = worker send period):
    • A BURNING cell ignites each of its 8 neighbours if:
        rand() < spread_prob + wind_boost × max(0, dot(neighbour_dir, wind))
    • A BURNING cell has a small chance (burnout_prob) of becoming BURNED.

Detection model
───────────────
  A drone at (x, y) detects the fire based on how many BURNING cells lie
  within DETECTION_RADIUS_M horizontal metres.  Detection probability
  scales with sqrt(visible_cell_count) up to a maximum, then floors at
  FALSE_ALARM_PROB (constant false-alarm rate even when fire count = 0).

Grid layout
───────────
  Origin (0, 0) = controller / launch pad position.
  Grid extends from (-GRID_HALF_M, -GRID_HALF_M) to (+GRID_HALF_M, +GRID_HALF_M).
  Fire ignition point is within this range — drones can fly over it.
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Grid parameters
# ---------------------------------------------------------------------------
GRID_CELLS: int = 120          # grid is GRID_CELLS × GRID_CELLS
CELL_M: float = 1.0            # metres per cell
GRID_HALF_M: float = (GRID_CELLS * CELL_M) / 2   # grid extends ±60m from origin

# Fire ignition point in world coordinates (metres from controller/origin)
IGNITION_XY_M: Tuple[float, float] = (20.0, 20.0)

# ---------------------------------------------------------------------------
# Fire dynamics
# ---------------------------------------------------------------------------
FIRE_STEP_INTERVAL_S: float = 0.5   # seconds between fire model steps (= 1/SEND_HZ)
BASE_SPREAD_PROB: float = 0.12      # base probability of igniting an adjacent cell
BURNOUT_PROB: float = 0.04          # probability a burning cell burns out each step
WIND_DIRECTION: Tuple[float, float] = (1.0, 0.4)  # (dx, dy) unnormalized — NE wind
WIND_BOOST: float = 0.20            # extra spread prob in the downwind direction

# ---------------------------------------------------------------------------
# Detection model
# ---------------------------------------------------------------------------
DETECTION_RADIUS_M: float = 40.0   # horizontal radius a drone can "see" burning cells
DETECT_MAX_P: float = 0.92         # prob at full coverage (sqrt-saturation ~20 cells)
DETECT_SATURATION: float = 20.0    # burning-cell count at which prob reaches ~max
FALSE_ALARM_PROB: float = 0.06     # detection prob when no burning cells visible

# Cell states
UNBURNED: int = 0
BURNING: int = 1
BURNED: int = 2


def _normalize(v: Tuple[float, float]) -> Tuple[float, float]:
    mag = math.sqrt(v[0] ** 2 + v[1] ** 2)
    return (v[0] / mag, v[1] / mag) if mag > 0 else (1.0, 0.0)


class FireGrid:
    """
    Deterministic, self-advancing cellular automaton fire model.

    Parameters
    ----------
    seed : int
        Controls the fire spread RNG.  Use the same seed across all workers
        in an experiment so they agree on the fire state.  Default 0.
    """

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._wind = _normalize(WIND_DIRECTION)

        # Allocate grid as a flat list of lists (pure Python, no numpy required)
        self._grid: List[List[int]] = [
            [UNBURNED] * GRID_CELLS for _ in range(GRID_CELLS)
        ]
        self.t: int = 0   # current step count

        # Record creation time so advance_to_wall_clock_step() advances relative
        # to experiment start, not Unix epoch (epoch / 0.5 ≈ 3.5B steps = hangs).
        self._start_s: float = time.time()

        # Ignite the starting cell
        ix, iy = self._world_to_cell(IGNITION_XY_M[0], IGNITION_XY_M[1])
        if 0 <= ix < GRID_CELLS and 0 <= iy < GRID_CELLS:
            self._grid[iy][ix] = BURNING

        # Snapshot of total burning cells (updated after each step)
        self._burning_count: int = 1

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _world_to_cell(self, wx: float, wy: float) -> Tuple[int, int]:
        """Convert world metres → grid (col, row) indices."""
        cx = int((wx + GRID_HALF_M) / CELL_M)
        cy = int((wy + GRID_HALF_M) / CELL_M)
        return cx, cy

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def step(self) -> None:
        """Advance the fire by one timestep."""
        new_grid = [row[:] for row in self._grid]   # shallow copy each row
        burning: int = 0

        for cy in range(GRID_CELLS):
            for cx in range(GRID_CELLS):
                if self._grid[cy][cx] != BURNING:
                    continue

                # Chance to burn out
                if self._rng.random() < BURNOUT_PROB:
                    new_grid[cy][cx] = BURNED
                    continue

                burning += 1

                # Try to ignite each of the 8 neighbours
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < GRID_CELLS and 0 <= nx < GRID_CELLS:
                            if self._grid[ny][nx] == UNBURNED:
                                # Wind boost: extra prob when spreading downwind
                                dot = dx * self._wind[0] + dy * self._wind[1]
                                p = BASE_SPREAD_PROB + WIND_BOOST * max(0.0, dot)
                                if self._rng.random() < p:
                                    new_grid[ny][nx] = BURNING

        self._grid = new_grid
        self._burning_count = burning
        self.t += 1

    def advance_to_wall_clock_step(self) -> None:
        """
        Advance the fire grid to match elapsed wall-clock time since creation.

        Target = int(elapsed_seconds / FIRE_STEP_INTERVAL_S).  Both workers
        create their FireGrid within milliseconds of each other at experiment
        start (both launched by mn_topo.py / run_experiments.py), so they
        track the same target and stay byte-for-byte identical throughout the
        run without any inter-process communication.

        Using elapsed time (not Unix epoch) avoids needing to advance billions
        of steps from a cold grid.
        """
        elapsed_s = time.time() - self._start_s
        target = int(elapsed_s / FIRE_STEP_INTERVAL_S)
        while self.t < target:
            self.step()

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def burning_near(self, wx: float, wy: float,
                     radius_m: float = DETECTION_RADIUS_M) -> int:
        """Count BURNING cells within `radius_m` horizontal metres of (wx, wy)."""
        cx_centre, cy_centre = self._world_to_cell(wx, wy)
        r_cells = radius_m / CELL_M

        x_lo = max(0, int(cx_centre - r_cells))
        x_hi = min(GRID_CELLS, int(cx_centre + r_cells) + 1)
        y_lo = max(0, int(cy_centre - r_cells))
        y_hi = min(GRID_CELLS, int(cy_centre + r_cells) + 1)

        count = 0
        for iy in range(y_lo, y_hi):
            for ix in range(x_lo, x_hi):
                if self._grid[iy][ix] == BURNING:
                    dist = math.sqrt((ix - cx_centre) ** 2 + (iy - cy_centre) ** 2)
                    if dist <= r_cells:
                        count += 1
        return count

    def detection_prob(self, drone_xy: Tuple[float, float]) -> float:
        """
        Probability this drone detects fire based on burning cells in range.

        Scales as sqrt(visible_count / DETECT_SATURATION), floored at
        FALSE_ALARM_PROB.  This gives:
          0 burning cells in range  → FALSE_ALARM_PROB  (~6%)
          ~5 cells                  → ~40%
          ~20 cells                 → ~92%  (near max)
        """
        count = self.burning_near(drone_xy[0], drone_xy[1])
        if count == 0:
            return FALSE_ALARM_PROB
        frac = min(1.0, math.sqrt(count / DETECT_SATURATION))
        return FALSE_ALARM_PROB + (DETECT_MAX_P - FALSE_ALARM_PROB) * frac

    def is_any_burning(self) -> bool:
        """True if at least one cell is currently BURNING (global fire ground truth)."""
        return self._burning_count > 0

    def total_burning(self) -> int:
        """Total number of currently BURNING cells in the entire grid."""
        return self._burning_count

    def total_burned(self) -> int:
        """Total cells that have already burned out (cumulative fire footprint)."""
        total = 0
        for row in self._grid:
            total += row.count(BURNED)
        return total
