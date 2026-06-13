# Investigation: High grid import at 2026-06-11T21:00:00+02:00

## Selected hour metrics
{
  "load_energy_wh": 8432.452843044655,
  "grid_consumption_energy_wh": 8056.165418897387,
  "grid_feedin_energy_wh": 0.0,
  "costs_amt": 2.0946030089133205,
  "revenue_amt": 0.0,
  "losses_energy_wh": 1520.0,
  "LiFePO4_Cluster_soc_factor": 0.34,
  "genetic_ac_charge_factor": 0.4,
  "genetic_dc_charge_factor": 0.0,
  "genetic_discharge_allowed_factor": 0.0,
  "LiFePO4_Cluster_grid_support_import_op_mode": 1.0,
  "LiFePO4_Cluster_grid_support_import_op_factor": 0.4,
  "LiFePO4_Cluster_peak_shaving_op_mode": 0.0,
  "LiFePO4_Cluster_peak_shaving_op_factor": 0.0
}

## Next day feed-in example hour
{
  "grid_feedin_energy_wh": 1449.2741972163403,
  "revenue_amt": 0.1637462451724882,
  "load_energy_wh": 687.3142422323056,
  "costs_amt": 0.0
}

## Relevant config
{
  "battery": {
    "capacity_wh": 37232,
    "min_soc_percentage": 5,
    "max_soc_percentage": 100,
    "levelized_cost_of_storage_kwh": 0.25,
    "max_charge_power_w": 19000.0
  },
  "prices": {
    "buy_example_eur_kwh": 0.26,
    "feed_in_example_eur_kwh": 0.113
  },
  "penalties": {
    "ac_charge_break_even": 1.0,
    "battery_soc_target_miss": 1.0,
    "dc_charge_feed_in_opportunity": 0.3,
    "ev_soc_late_per_hour": 2.0,
    "ev_soc_miss": 10,
    "ev_soc_target_miss": 10.0
  },
  "inverter": {
    "max_ac_charge_power_w": 8000.0,
    "ac_to_dc_efficiency": 0.9,
    "dc_to_ac_efficiency": 0.95
  }
}

## Derived explanation
- In selected hour, optimizer chooses GRID_SUPPORT_IMPORT and ac_charge_factor=0.4, hence battery charging from grid dominates load_energy_wh.
- battery_soc_target_miss penalty is enabled (=1.0). Code penalizes battery SOC below max SOC at *first sunset* in horizon.
- With optimization starting at 21:00 and small PV still present at 21:00/22:00, first sunset occurs immediately after 22:00, creating a strong short-term incentive to charge now.
- This can override pure buy-vs-feed-in arbitrage intuition, especially with coarse 1h intervals and SOC targets.
