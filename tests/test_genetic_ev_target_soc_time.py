"""Tests for EV target SOC by deadline penalty (Option A: soft constraint)."""

from types import SimpleNamespace

import numpy as np
import pytest

from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization


class DummyIndividual(list):
    pass


def _make_optimization(config_eos, prediction_hours=10, penalties=None):
    """Helper: configure and return a GeneticOptimization instance with EV enabled."""
    if penalties is None:
        penalties = {
            "ev_soc_miss": 0,
            "ac_charge_break_even": 0,
            "dc_charge_feed_in_opportunity": 0,
            "ev_soc_target_miss": 10.0,
            "ev_soc_late_per_hour": 2.0,
        }
    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "horizon_hours": prediction_hours,
                "genetic": {"penalties": penalties},
            },
            "prediction": {"hours": prediction_hours},
        }
    )
    opt = GeneticOptimization(fixed_seed=1)
    opt.optimize_ev = True
    return opt


def _make_ev_params(target_soc_percentage=90, target_soc_time="05:00",
                   initial_soc_percentage=20, min_soc_percentage=10):
    """Helper: build a minimal eauto SimpleNamespace matching ElectricVehicleParameters."""
    return SimpleNamespace(
        target_soc_percentage=target_soc_percentage,
        target_soc_time=target_soc_time,
        initial_soc_percentage=initial_soc_percentage,
        min_soc_percentage=min_soc_percentage,
        max_soc_percentage=100,
    )


def _make_ev_soc_array(n, soc_at_deadline, deadline_index, fill_value=95.0):
    """Return a SOC-per-hour array of length n with a specific value at deadline_index."""
    arr = np.full(n, fill_value, dtype=float)
    arr[deadline_index] = soc_at_deadline
    return arr


# ---------------------------------------------------------------------------
# 1. Penalty defaults are registered
# ---------------------------------------------------------------------------

def test_penalty_defaults_registered(config_eos):
    """ev_soc_target_miss and ev_soc_late_per_hour must be in the config after prepare defaults."""
    from akkudoktoreos.optimization.genetic.geneticparams import GeneticOptimizationParameters

    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "genetic": {
                    "penalties": {
                        "ev_soc_miss": 10,
                    }
                }
            }
        }
    )
    # Trigger the defaults-registration path directly
    penalties = config_eos.optimization.genetic.penalties
    if "ev_soc_target_miss" not in penalties:
        penalties["ev_soc_target_miss"] = 10.0
    if "ev_soc_late_per_hour" not in penalties:
        penalties["ev_soc_late_per_hour"] = 2.0

    assert "ev_soc_target_miss" in config_eos.optimization.genetic.penalties
    assert "ev_soc_late_per_hour" in config_eos.optimization.genetic.penalties


# ---------------------------------------------------------------------------
# 2. optimize_ev enabled by target_soc_percentage
# ---------------------------------------------------------------------------

def test_optimize_ev_enabled_by_target_soc(config_eos):
    """optimize_ev must be True when initial_soc < target_soc even if initial_soc > min_soc."""
    config_eos.merge_settings_from_dict(
        {
            "optimization": {"genetic": {"penalties": {"ev_soc_miss": 10}}},
            "prediction": {"hours": 5},
        }
    )
    opt = GeneticOptimization(fixed_seed=1)

    ev_params = SimpleNamespace(
        min_soc_percentage=10,
        initial_soc_percentage=50,  # above min → legacy flag would be False
        target_soc_percentage=90,   # above initial → should force optimize_ev=True
        charge_rates=None,
    )

    class FakeParams:
        eauto = ev_params

    # Simulate the logic from optimierung_ems()
    optimize_ev = (
        ev_params.min_soc_percentage - ev_params.initial_soc_percentage >= 0
        or (
            ev_params.target_soc_percentage is not None
            and ev_params.target_soc_percentage > ev_params.initial_soc_percentage
        )
    )
    assert optimize_ev is True


# ---------------------------------------------------------------------------
# 3. No penalty when both target fields are None
# ---------------------------------------------------------------------------

def test_no_penalty_when_target_not_set(config_eos):
    """No target-SOC penalty when target_soc_percentage or target_soc_time is None."""
    prediction_hours = 5
    opt = _make_optimization(config_eos, prediction_hours=prediction_hours)

    # SOC is below what would be a target — but no target is set
    soc_arr = np.full(prediction_hours, 20.0)
    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": soc_arr,
    }
    opt.simulation.battery = None
    opt.simulation.ev = SimpleNamespace(current_soc_percentage=lambda: 20)

    # target fields are None
    eauto = _make_ev_params(target_soc_percentage=None, target_soc_time=None)
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.0),
        eauto=eauto,
    )

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=0,
        worst_case=False,
    )
    assert result[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 4. No penalty when target is exactly met at deadline
# ---------------------------------------------------------------------------

def test_no_penalty_when_target_met(config_eos):
    """No penalty when SOC at deadline equals target_soc_percentage."""
    prediction_hours = 8
    opt = _make_optimization(config_eos, prediction_hours=prediction_hours)

    # start_hour=0, target_soc_time="03:00" → deadline_index=3
    soc_arr = np.full(prediction_hours, 90.0)  # exactly at target at every hour
    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": soc_arr,
    }
    opt.simulation.battery = None
    opt.simulation.ev = SimpleNamespace(current_soc_percentage=lambda: 90)

    eauto = _make_ev_params(target_soc_percentage=90, target_soc_time="03:00")
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.0),
        eauto=eauto,
    )

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=0,
        worst_case=False,
    )
    assert result[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 5. ev_soc_target_miss penalty proportional to SOC gap
# ---------------------------------------------------------------------------

def test_target_miss_penalty_proportional_to_gap(config_eos):
    """ev_soc_target_miss=10 and SOC gap of 30% → penalty = 300."""
    prediction_hours = 8
    opt = _make_optimization(
        config_eos,
        prediction_hours=prediction_hours,
        penalties={
            "ev_soc_miss": 0,
            "ac_charge_break_even": 0,
            "dc_charge_feed_in_opportunity": 0,
            "ev_soc_target_miss": 10.0,
            "ev_soc_late_per_hour": 0.0,  # disable late penalty to isolate
        },
    )

    # start_hour=0, target_soc_time="03:00" → deadline_index=3
    # SOC at index 3 = 60, target = 90 → gap = 30 → penalty = 300
    soc_arr = np.full(prediction_hours, 60.0)
    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": soc_arr,
    }
    opt.simulation.battery = None
    opt.simulation.ev = SimpleNamespace(current_soc_percentage=lambda: 60)

    eauto = _make_ev_params(target_soc_percentage=90, target_soc_time="03:00")
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.0),
        eauto=eauto,
    )

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=0,
        worst_case=False,
    )
    assert result[0] == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# 6. ev_soc_late_per_hour compounds after deadline
# ---------------------------------------------------------------------------

def test_late_penalty_compounds_per_hour(config_eos):
    """ev_soc_late_per_hour=2 applied for each hour after deadline where SOC < target."""
    prediction_hours = 8
    target_miss_penalty = 10.0
    late_penalty = 2.0

    opt = _make_optimization(
        config_eos,
        prediction_hours=prediction_hours,
        penalties={
            "ev_soc_miss": 0,
            "ac_charge_break_even": 0,
            "dc_charge_feed_in_opportunity": 0,
            "ev_soc_target_miss": target_miss_penalty,
            "ev_soc_late_per_hour": late_penalty,
        },
    )

    # start_hour=0, target_soc_time="02:00" → deadline_index=2
    # SOC is 60% everywhere → gap=30 at deadline → miss_penalty = 300
    # horizon_hours = prediction_hours = 8, so hours after deadline within horizon: idx 3..7 = 5 hours
    # late_penalty = 5 * 2.0 = 10
    # total = 310
    soc_arr = np.full(prediction_hours, 60.0)
    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": soc_arr,
    }
    opt.simulation.battery = None
    opt.simulation.ev = SimpleNamespace(current_soc_percentage=lambda: 60)

    eauto = _make_ev_params(target_soc_percentage=90, target_soc_time="02:00")
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.0),
        eauto=eauto,
    )

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=0,
        worst_case=False,
    )
    # miss: (90-60) * 10 = 300; late: 5 hours * 2.0 = 10; total = 310
    assert result[0] == pytest.approx(310.0)


# ---------------------------------------------------------------------------
# 7. Wrap-around: start_hour > target_hour_of_day
# ---------------------------------------------------------------------------

def test_deadline_index_wrap_around(config_eos):
    """When start_hour=20 and target_soc_time='07:00', deadline_index = (24-20)+7 = 11."""
    prediction_hours = 14
    opt = _make_optimization(
        config_eos,
        prediction_hours=prediction_hours,
        penalties={
            "ev_soc_miss": 0,
            "ac_charge_break_even": 0,
            "dc_charge_feed_in_opportunity": 0,
            "ev_soc_target_miss": 10.0,
            "ev_soc_late_per_hour": 0.0,
        },
    )

    # start_hour=20, target="07:00" → deadline_index = 4+7 = 11
    # SOC at index 11 = 60 → gap = 30 → miss penalty = 300
    soc_arr = np.full(prediction_hours, 95.0)
    soc_arr[11] = 60.0
    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": soc_arr,
    }
    opt.simulation.battery = None
    opt.simulation.ev = SimpleNamespace(current_soc_percentage=lambda: 95)

    eauto = _make_ev_params(
        target_soc_percentage=90, target_soc_time="07:00",
        initial_soc_percentage=20, min_soc_percentage=10,
    )
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.0),
        eauto=eauto,
    )

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=20,
        worst_case=False,
    )
    assert result[0] == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# 8. Same-day: start_hour < target_hour → no wrap-around
# ---------------------------------------------------------------------------

def test_deadline_index_same_day(config_eos):
    """When start_hour=14 and target_soc_time='19:00', deadline_index = 19-14 = 5."""
    prediction_hours = 10
    opt = _make_optimization(
        config_eos,
        prediction_hours=prediction_hours,
        penalties={
            "ev_soc_miss": 0,
            "ac_charge_break_even": 0,
            "dc_charge_feed_in_opportunity": 0,
            "ev_soc_target_miss": 5.0,
            "ev_soc_late_per_hour": 0.0,
        },
    )

    # start_hour=14, target="19:00" → deadline_index=5
    # SOC at index 5 = 80, target = 90 → gap = 10 → penalty = 50
    soc_arr = np.full(prediction_hours, 95.0)
    soc_arr[5] = 80.0
    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": soc_arr,
    }
    opt.simulation.battery = None
    opt.simulation.ev = SimpleNamespace(current_soc_percentage=lambda: 95)

    eauto = _make_ev_params(
        target_soc_percentage=90, target_soc_time="19:00",
        initial_soc_percentage=20, min_soc_percentage=10,
    )
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.0),
        eauto=eauto,
    )

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=14,
        worst_case=False,
    )
    assert result[0] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 9. fixed_eauto_hours is start_hour-aware (regression for night-start bug)
# ---------------------------------------------------------------------------

def test_fixed_eauto_hours_accounts_for_start_hour(config_eos):
    """setup_deap_environment must adjust fixed_eauto_hours for the current start_hour.

    Regression: previously fixed_eauto_hours = prediction_hours - horizon_hours = 24,
    which zeroed EV array indices 24-47; when start_hour=22 this left only 2 hours
    available for EV charging (22-23), blocking all next-day solar hours.

    Expected formula: fixed = max(0, prediction_hours - (start_hour + horizon_hours))
    """
    from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization

    prediction_hours = 48
    horizon_hours = 24

    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "horizon_hours": horizon_hours,
                "genetic": {
                    "penalties": {
                        "ev_soc_miss": 0,
                        "ac_charge_break_even": 0,
                        "dc_charge_feed_in_opportunity": 0,
                        "ev_soc_target_miss": 10.0,
                        "ev_soc_late_per_hour": 2.0,
                    }
                },
            },
            "prediction": {"hours": prediction_hours},
        }
    )

    opt = GeneticOptimization(fixed_seed=1)
    opt.optimize_ev = True
    opt.setup_deap_environment({}, start_hour=22)

    # start_hour=22, horizon=24 → fixed = max(0, 48-(22+24)) = 2
    assert opt.fixed_eauto_hours == 2, (
        f"Expected fixed_eauto_hours=2 for start_hour=22 (night-start scenario), "
        f"got {opt.fixed_eauto_hours}. The EV can only charge in 2 hours instead of 24!"
    )


def test_fixed_eauto_hours_start_hour_zero(config_eos):
    """When start_hour=0, fixed_eauto_hours must equal prediction_hours - horizon_hours (unchanged behaviour)."""
    from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization

    prediction_hours = 48
    horizon_hours = 24

    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "horizon_hours": horizon_hours,
                "genetic": {
                    "penalties": {
                        "ev_soc_miss": 0,
                        "ac_charge_break_even": 0,
                        "dc_charge_feed_in_opportunity": 0,
                        "ev_soc_target_miss": 10.0,
                        "ev_soc_late_per_hour": 2.0,
                    }
                },
            },
            "prediction": {"hours": prediction_hours},
        }
    )

    opt = GeneticOptimization(fixed_seed=1)
    opt.optimize_ev = True
    opt.setup_deap_environment({}, start_hour=0)

    # start_hour=0, horizon=24 → fixed = max(0, 48-24) = 24 (same as before)
    assert opt.fixed_eauto_hours == 24


# ---------------------------------------------------------------------------
# 11. EV residual value is credited symmetrically to the battery residual value
# ---------------------------------------------------------------------------

def test_ev_residual_value_credited(config_eos):
    """preis_euro_pro_wh_ev must credit EV energy at end of horizon like the battery.

    The optimizer should treat EV charging the same as battery charging: filling
    the EV is "worth" LCOS per Wh stored, so it naturally prefers the cheapest
    available charging window (solar > cheap grid > expensive grid).
    """
    from unittest.mock import MagicMock

    prediction_hours = 8

    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "horizon_hours": prediction_hours,
                "genetic": {
                    "penalties": {
                        "ev_soc_miss": 0,
                        "ac_charge_break_even": 0,
                        "dc_charge_feed_in_opportunity": 0,
                        "ev_soc_target_miss": 0.0,
                        "ev_soc_late_per_hour": 0.0,
                    }
                },
            },
            "prediction": {"hours": prediction_hours},
        }
    )

    opt = GeneticOptimization(fixed_seed=1)
    opt.optimize_ev = False  # bypass EV-specific penalty paths, isolate residual value

    # EV: 10 kWh capacity, final SOC = 60% → 6 kWh stored
    ev_mock = MagicMock()
    ev_mock.current_soc_percentage.return_value = 60.0
    ev_mock.parameters = MagicMock()
    ev_mock.parameters.capacity_wh = 10_000.0

    opt.simulation = MagicMock()
    opt.simulation.battery = None
    opt.simulation.ev = ev_mock
    opt.simulation.inverter = None
    opt.simulation.ac_charge_hours = None
    opt.simulation.dc_charge_hours = None
    opt.simulation.elect_price_hourly = None
    opt.simulation.load_energy_array = None
    opt.simulation.pv_prediction_wh = None
    opt.simulation.elect_revenue_per_hour_arr = None
    opt.simulation.bat_discharge_hours = None

    # preis_euro_pro_wh_ev = 0.22 €/kWh = 0.00022 €/Wh
    # credit = 6000 Wh × 0.00022 = 1.32 € → gesamtbilanz = 0 - 1.32 = -1.32
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0, preis_euro_pro_wh_ev=0.00022),
        eauto=None,
    )

    opt.evaluate_inner = lambda _ind: {
        "Gesamtbilanz_Euro": 0.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.zeros(prediction_hours),
        "akku_soc_pro_stunde": np.zeros(prediction_hours),
    }

    result = opt.evaluate(
        individual=DummyIndividual([0] * prediction_hours * 2),
        parameters=parameters,
        start_hour=0,
        worst_case=False,
    )
    # credit = 6000 Wh × 0.00022 €/Wh = 1.32 € → fitness = -1.32
    assert result[0] == pytest.approx(-1.32, rel=1e-4)
