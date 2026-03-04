"""Genetic optimization algorithm device interfaces/ parameters."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.utils.datetimeutil import TimeWindowSequence


class DeviceParameters(GeneticParametersBaseModel):
    device_id: str = Field(json_schema_extra={"description": "ID of device", "examples": "device1"})
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Number of prediction hours. Defaults to global config prediction hours.",
            "examples": [None],
        },
    )


def max_charging_power_field(description: Optional[str] = None) -> float:
    if description is None:
        description = "Maximum charging power in watts."
    return Field(default=5000, gt=0, json_schema_extra={"description": description})


def initial_soc_percentage_field(description: str) -> int:
    return Field(
        default=0, ge=0, le=100, json_schema_extra={"description": description, "examples": [42]}
    )


def discharging_efficiency_field(default_value: float) -> float:
    return Field(
        default=default_value,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the discharge efficiency of the battery."
        },
    )


class BaseBatteryParameters(DeviceParameters):
    """Battery Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of battery", "examples": ["battery1"]}
    )
    capacity_wh: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the capacity of the battery in watt-hours.",
            "examples": [8000],
        },
    )
    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the charging efficiency of the battery."
        },
    )
    discharging_efficiency: float = discharging_efficiency_field(0.88)
    max_charge_power_w: Optional[float] = max_charging_power_field()
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the state of charge of the battery at the **start** of the current hour (not the current state)."
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "An integer representing the minimum state of charge (SOC) of the battery in percentage.",
            "examples": [10],
        },
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "An integer representing the maximum state of charge (SOC) of the battery in percentage."
        },
    )
    charge_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": "Charge rates as factor of maximum charging power [0.00 ... 1.00]. None denotes all charge rates are available.",
            "examples": [[0.0, 0.25, 0.5, 0.75, 1.0], None],
        },
    )


class SolarPanelBatteryParameters(BaseBatteryParameters):
    """PV battery device simulation configuration."""

    max_charge_power_w: Optional[float] = max_charging_power_field()


class ElectricVehicleParameters(BaseBatteryParameters):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )


class HomeApplianceParameters(DeviceParameters):
    """Home Appliance Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of home appliance", "examples": ["dishwasher"]}
    )
    consumption_wh: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the energy consumption of a household device in watt-hours.",
            "examples": [2000],
        },
    )
    duration_h: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the usage duration of a household device in hours.",
            "examples": [3],
        },
    )
    time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "description": "List of allowed time windows. Defaults to optimization general time window.",
            "examples": [
                [
                    {"start_time": "10:00", "duration": "2 hours"},
                ],
            ],
        },
    )


class InverterParameters(DeviceParameters):
    """Inverter Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of inverter", "examples": ["inverter1"]}
    )
    max_power_wh: float = Field(gt=0, json_schema_extra={"examples": [10000]})
    battery_id: Optional[str] = Field(
        default=None,
        json_schema_extra={"description": "ID of battery", "examples": [None, "battery1"]},
    )
    ac_to_dc_efficiency: float = Field(
        default=1.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of AC to DC conversion (for AC/grid charging of battery). "
                "Set to 0 to disable AC charging via inverter. "
                "Default 1.0 for backward compatibility (no additional inverter loss)."
            ),
            "examples": [0.95, 1.0, 0.0],
        },
    )
    dc_to_ac_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of DC to AC conversion (for battery discharging to AC load/grid). "
                "Default 1.0 for backward compatibility (no additional inverter loss)."
            ),
            "examples": [0.95, 1.0],
        },
    )
    max_ac_charge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum AC charging power in watts. "
                "None means no additional limit (battery's own max_charge_power_w applies). "
                "Set to 0 to disable AC charging."
            ),
            "examples": [None, 0, 5000],
        },
    )


class HybridPVInverterParameters(DeviceParameters):
    """Hybrid PV inverter parameters for feed-in mode planning."""

    peakpower_kw: float = Field(
        gt=0,
        json_schema_extra={
            "description": "Nominal peak power of the PV plant [kWp].",
            "examples": [4.875],
        },
    )
    feed_in_tariff_full_kwh: float = Field(
        ge=0,
        json_schema_extra={
            "description": "Feed-in tariff in FULL_FEED_IN mode [€/kWh].",
            "examples": [0.12],
        },
    )
    feed_in_tariff_excess_kwh: float = Field(
        ge=0,
        json_schema_extra={
            "description": "Feed-in tariff in EXCESS mode [€/kWh].",
            "examples": [0.082],
        },
    )
    mode: str = Field(
        default="EXCESS",
        json_schema_extra={
            "description": "Initial feed-in mode.",
            "examples": ["EXCESS", "FULL_FEED_IN"],
        },
    )
    max_mode_switches_per_day: int = Field(
        default=6,
        ge=0,
        json_schema_extra={
            "description": "Soft switch limit per 24h.",
            "examples": [6],
        },
    )
    min_production_threshold_w: float = Field(
        default=10.0,
        ge=0,
        json_schema_extra={
            "description": "Force EXCESS below this production threshold [W].",
            "examples": [10.0],
        },
    )
    minutes_before_sunset: int = Field(
        default=30,
        ge=0,
        json_schema_extra={
            "description": "Force EXCESS this many minutes before sunset.",
            "examples": [30],
        },
    )
    standby_loss_w: float = Field(
        default=1.0,
        ge=0,
        json_schema_extra={
            "description": "Standby losses in FULL_FEED_IN mode [W].",
            "examples": [1.0],
        },
    )
    switch_penalty_base_eur: float = Field(
        default=0.02,
        ge=0,
        json_schema_extra={
            "description": "Base penalty for soft-limit switch violations [€].",
            "examples": [0.02],
        },
    )
    switch_penalty_exp: float = Field(
        default=1.8,
        ge=1.0,
        json_schema_extra={
            "description": "Exponent for switch penalty growth.",
            "examples": [1.8],
        },
    )
    forecast_share: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        json_schema_extra={
            "description": "Optional explicit forecast share [0..1].",
            "examples": [0.25, None],
        },
    )
