from types import SimpleNamespace

import numpy as np
import pytest

from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization


class DummyIndividual(list):
    pass


class DummyBattery:
    max_charge_power_w = 1000.0

    def current_energy_content(self) -> float:
        return 0.0


def test_optimize_dc_charge_flag_from_config(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "genetic": {
                    "optimize_dc_charge": True,
                },
            },
        }
    )

    optimization = GeneticOptimization(fixed_seed=1)
    assert optimization.optimize_dc_charge is True


def test_dc_charge_feed_in_opportunity_penalty(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "genetic": {
                    "optimize_dc_charge": True,
                    "penalties": {
                        "ev_soc_miss": 10,
                        "ac_charge_break_even": 0,
                        "dc_charge_feed_in_opportunity": 1.0,
                    },
                },
            },
            "prediction": {
                "hours": 3,
            },
        }
    )

    optimization = GeneticOptimization(fixed_seed=1)
    optimization.optimize_ev = False

    optimization.evaluate_inner = lambda _individual: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
    }

    optimization.simulation.battery = DummyBattery()
    optimization.simulation.inverter = None
    optimization.simulation.ac_charge_hours = np.array([0.0, 0.0, 0.0], dtype=float)
    optimization.simulation.dc_charge_hours = np.array([1.0, 1.0, 1.0], dtype=float)
    optimization.simulation.elect_revenue_per_hour_arr = np.array([0.20, 0.05, 0.10], dtype=float)
    optimization.simulation.pv_prediction_wh = np.array([5000.0, 5000.0, 5000.0], dtype=float)
    optimization.simulation.load_energy_array = np.array([1000.0, 1000.0, 1000.0], dtype=float)

    # Expected penalty with reference=min(feed-in)=0.05:
    # hour0: 1000 Wh * (0.20 - 0.05) = 150
    # hour1: 1000 Wh * (0.05 - 0.05) = 0
    # hour2: 1000 Wh * (0.10 - 0.05) = 50
    # total = 200
    expected_penalty = 200.0

    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0),
        eauto=None,
    )
    result = optimization.evaluate(
        individual=DummyIndividual([0, 0, 0]),
        parameters=parameters,
        start_hour=0,
        worst_case=False,
    )

    assert result[0] == pytest.approx(expected_penalty)
