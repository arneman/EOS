import pytest

from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticdevices import HybridPVInverterParameters
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)


def _base_parameters(hybrid_pv_inverters: list[HybridPVInverterParameters]) -> GeneticOptimizationParameters:
    horizon = 24
    pv = [0.0] * horizon
    prices = [0.0003] * horizon
    load = [500.0] * horizon
    feedin = [0.00008] * horizon

    return GeneticOptimizationParameters(
        ems=GeneticEnergyManagementParameters(
            pv_prognose_wh=pv,
            strompreis_euro_pro_wh=prices,
            einspeiseverguetung_euro_pro_wh=feedin,
            preis_euro_pro_wh_akku=0.0,
            gesamtlast=load,
        ),
        pv_akku=None,
        inverter=None,
        eauto=None,
        dishwasher=None,
        hybrid_pv_inverters=hybrid_pv_inverters,
    )


def test_hybrid_pv_forced_excess_near_sunset_and_low_production(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "optimization": {"interval": 3600, "horizon_hours": 24},
        }
    )

    optimization = GeneticOptimization(fixed_seed=42)

    inverter = HybridPVInverterParameters(
        device_id="pv1",
        peakpower_kw=4.875,
        feed_in_tariff_full_kwh=0.12,
        feed_in_tariff_excess_kwh=0.082,
        min_production_threshold_w=10.0,
        minutes_before_sunset=30,
    )

    params = _base_parameters([inverter])
    # Significant production around noon, no production at edges.
    params.ems.pv_prognose_wh = [0.0] * 6 + [50.0] * 10 + [0.0] * 8

    plans, _, _ = optimization._build_hybrid_pv_plans(params)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.mode_schedule[-1] == "EXCESS"
    assert plan.forced_excess[-1] is True
    assert all(mode == "EXCESS" for mode in plan.mode_schedule[:6])


def test_hybrid_pv_switch_penalty_applies_when_switching_is_high(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "optimization": {"interval": 3600, "horizon_hours": 24},
        }
    )

    optimization = GeneticOptimization(fixed_seed=42)

    inverter = HybridPVInverterParameters(
        device_id="pv1",
        peakpower_kw=4.875,
        feed_in_tariff_full_kwh=0.12,
        feed_in_tariff_excess_kwh=0.082,
        max_mode_switches_per_day=6,
        switch_penalty_base_eur=0.1,
        switch_penalty_exp=2.0,
        min_production_threshold_w=10.0,
        minutes_before_sunset=0,
    )

    params = _base_parameters([inverter])
    # Alternating production around the threshold creates frequent mode changes.
    params.ems.pv_prognose_wh = [20.0 if i % 2 == 0 else 0.0 for i in range(24)]

    plans, _, total_penalty = optimization._build_hybrid_pv_plans(params)

    assert len(plans) == 1
    assert plans[0].switch_count > 6
    assert plans[0].estimated_penalty_eur > 0
    assert total_penalty == pytest.approx(plans[0].estimated_penalty_eur)


def test_hybrid_pv_multi_inverter_independent_plans(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "optimization": {"interval": 3600, "horizon_hours": 24},
        }
    )

    optimization = GeneticOptimization(fixed_seed=42)

    inverters = [
        HybridPVInverterParameters(
            device_id="pv_small",
            peakpower_kw=2.0,
            feed_in_tariff_full_kwh=0.10,
            feed_in_tariff_excess_kwh=0.08,
        ),
        HybridPVInverterParameters(
            device_id="pv_big",
            peakpower_kw=5.0,
            feed_in_tariff_full_kwh=0.14,
            feed_in_tariff_excess_kwh=0.08,
        ),
    ]

    params = _base_parameters(inverters)
    params.ems.pv_prognose_wh = [0.0] * 5 + [1000.0] * 12 + [0.0] * 7

    plans, total_revenue, total_penalty = optimization._build_hybrid_pv_plans(params)

    assert len(plans) == 2
    assert {plan.device_id for plan in plans} == {"pv_small", "pv_big"}
    assert total_revenue > 0
    assert total_penalty >= 0
