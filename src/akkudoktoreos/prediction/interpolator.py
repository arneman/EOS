#!/usr/bin/env python
import math
import pickle
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from akkudoktoreos.core.coreabc import SingletonMixin


class SelfConsumptionProbabilityInterpolator:
    def __init__(self, filepath: str | Path):
        self.filepath = filepath
        # Load the RegularGridInterpolator
        with open(self.filepath, "rb") as file:
            self.interpolator: RegularGridInterpolator = pickle.load(file)  # noqa: S301

        # Precompute cumulative sum lookup table for O(1) self-consumption calculation.
        # Since the interpolator grid uses a uniform 50W step on both axes, and
        # calculate_self_consumption always evaluates at PV grid points (multiples of 50),
        # the bilinear interpolation reduces to a weighted sum of precomputed partial sums.
        values = self.interpolator.values
        self._n_load = values.shape[0]  # number of load grid points
        self._n_pv = values.shape[1]  # number of PV grid points
        self._load_step = float(self.interpolator.grid[0][1] - self.interpolator.grid[0][0])
        self._load_max = float(self.interpolator.grid[0][-1])
        # cumsum[i, k] = sum of values[i, 0:k] (prepend zero column for k=0)
        self._cumsum = np.zeros((self._n_load, self._n_pv + 1))
        self._cumsum[:, 1:] = np.cumsum(values, axis=1)

    def calculate_self_consumption(self, load_1h_power: float, pv_power: float) -> float:
        """Calculate the PV self-consumption rate.

        Uses a precomputed cumulative sum lookup table for O(1) evaluation,
        giving mathematically identical results to the full RegularGridInterpolator
        evaluation over partial loads.

        Args:
         - load_1h_power: 1h power level (W).
         - pv_power: Current PV power output (W).

        Returns:
         - Self-consumption rate as a float.
        """
        # Number of PV grid points to sum (matches np.arange(0, pv_power + 50, 50))
        n_pv_points = min(math.ceil((pv_power + 50.0) / 50.0), self._n_pv)

        # Out-of-bounds load returns 0 (matches interpolator fill_value=0)
        if load_1h_power < 0.0 or load_1h_power > self._load_max:
            return 0.0

        # Find load grid position for bilinear weighting
        idx = load_1h_power / self._load_step
        i = int(idx)
        if i >= self._n_load - 1:
            # At or beyond last grid point — clamp
            i = self._n_load - 2
            frac = 1.0
        else:
            frac = idx - i

        # Weighted sum of precomputed cumulative values — exact bilinear result
        return (1.0 - frac) * self._cumsum[i, n_pv_points] + frac * self._cumsum[i + 1, n_pv_points]


class EOSLoadInterpolator(SelfConsumptionProbabilityInterpolator, SingletonMixin):
    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        filename = Path(__file__).parent.resolve() / ".." / "data" / "regular_grid_interpolator.pkl"
        super().__init__(filename)


# Initialize the Energy Management System, it is a singleton.
eos_load_interpolator = EOSLoadInterpolator()


def get_eos_load_interpolator() -> EOSLoadInterpolator:
    return eos_load_interpolator
