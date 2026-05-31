#!/usr/bin/env python3
"""
MQTT to EOS Bridge
==================

Connects to MQTT broker and forwards device measurements to EOS REST API.

MQTT Topics:
- devices/bmw_i5/cardata/drivetrain/batteryManagement/header → BMW_i5-soc-factor
- devices/victron_battery/battery_soc → LiFePO4_Cluster-soc-factor (combined virtual SOC)
- devices/bmw_i5//battery_missing_until_max_soc_wh → virtual battery capacity update

Virtual Battery Strategy:
  EOS battery capacity is inflated by the EV's energy deficit so that EOS
  plans enough PV charging for both battery and EV. The reported SOC is
  battery_stored_wh / (battery_capacity + ev_deficit_wh). This makes EOS
  plan more charging hours without any code changes to EOS itself.

Configuration via environment variables:
- MQTT_BROKER (default: mqtt.fritz.box)
- MQTT_PORT (default: 1883)
- MQTT_USER (default: mqtt_user)
- MQTT_PASSWORD (required)
- EOS_URL (default: http://localhost:8503)
- LOG_LEVEL (default: INFO)

Usage:
    export MQTT_PASSWORD="your-password"
    python scripts/mqtt_eos_bridge.py
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Dict, Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("ERROR: paho-mqtt not installed.")
    print("Install with: pip install paho-mqtt")
    sys.exit(1)

import requests
from loguru import logger

# =============================================================================
# Defaults (changeable values)
# =============================================================================

DEFAULT_MQTT_BROKER = "mqtt.fritz.box"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_USER = "mqtt_user"
DEFAULT_EOS_URL = "http://localhost:8503"
DEFAULT_LOG_LEVEL = "INFO"

EOS_MEASUREMENT_PATH = "/v1/measurement/value"
EOS_CONFIG_PATH = "/v1/config/devices/batteries/0"
EOS_HEALTH_PATH = "/docs"

EOS_PUT_TIMEOUT_S = 5
EOS_HEALTH_TIMEOUT_S = 5
EOS_SEND_INTERVAL_S = 60
EOS_POLL_INTERVAL_S = 30  # How often to poll EOS solution (baseline)
EOS_HEALTH_CHECK_INTERVAL_S = 5  # How often to check for a new optimization result
MQTT_KEEPALIVE_S = 60

# MQTT Publish topics (EOS → MQTT)
MQTT_PUB_PREFIX = "eos"
MQTT_PUB_BATTERY_MODE = f"{MQTT_PUB_PREFIX}/battery/operation_mode"
MQTT_PUB_BATTERY_FACTOR = f"{MQTT_PUB_PREFIX}/battery/operation_mode_factor"
MQTT_PUB_BATTERY_CHARGE = f"{MQTT_PUB_PREFIX}/battery/charge_allowed"
MQTT_PUB_BATTERY_DISCHARGE = f"{MQTT_PUB_PREFIX}/battery/discharge_allowed"
MQTT_PUB_BATTERY_POWER = f"{MQTT_PUB_PREFIX}/battery/charge_power_w"
MQTT_PUB_EV_CHARGE = f"{MQTT_PUB_PREFIX}/ev/charge_allowed"
MQTT_PUB_EV_POWER = f"{MQTT_PUB_PREFIX}/ev/charge_power_w"
MQTT_PUB_SCHEDULE = f"{MQTT_PUB_PREFIX}/schedule"

# MQTT Topics
TOPIC_BMW_SOC = "devices/bmw_i5/cardata/drivetrain/batteryManagement/header"
TOPIC_BATTERY_SOC = "devices/victron_battery/battery_soc"
TOPIC_EV_DEFICIT = "devices/bmw_i5/battery_missing_until_max_soc_wh"
TOPIC_EV_PLUGGED = "devices/bmw_i5/is_plugged"
TOPIC_EV_MAX_POWER = "devices/bmw_i5/max_power"

# EOS measurement keys
BMW_SOC_EOS_KEY = "BMW_i5-soc-factor"
BATTERY_SOC_EOS_KEY = "LiFePO4_Cluster-soc-factor"

# Descriptions
BMW_SOC_DESCRIPTION = "BMW i5 State of Charge"
BATTERY_SOC_DESCRIPTION = "Battery State of Charge"

# Real battery hardware constants
REAL_BATTERY_CAPACITY_WH = 30412
REAL_BATTERY_MAX_CHARGE_W = 8000
EV_MAX_CHARGE_W_DEFAULT = 11000  # Used until MQTT publishes actual value

SOC_SCALE_FACTOR = 100.0

# =============================================================================
# Configuration
# =============================================================================

MQTT_BROKER = os.getenv("MQTT_BROKER", DEFAULT_MQTT_BROKER)
MQTT_PORT = int(os.getenv("MQTT_PORT", str(DEFAULT_MQTT_PORT)))
MQTT_USER = os.getenv("MQTT_USER", DEFAULT_MQTT_USER)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

if not MQTT_PASSWORD:
    print("ERROR: MQTT_PASSWORD environment variable not set")
    print("Usage: export MQTT_PASSWORD='your-password'")
    sys.exit(1)

EOS_URL = os.getenv("EOS_URL", DEFAULT_EOS_URL)
EOS_MEASUREMENT_ENDPOINT = f"{EOS_URL}{EOS_MEASUREMENT_PATH}"

LOG_LEVEL = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL)

# =============================================================================
# MQTT Topic to EOS Measurement Key Mapping
# =============================================================================

TOPIC_MAPPING = {
    TOPIC_BMW_SOC: {
        "eos_key": BMW_SOC_EOS_KEY,
        "converter": lambda x: float(x) / SOC_SCALE_FACTOR,  # MQTT sends 0-100%
        "description": BMW_SOC_DESCRIPTION,
    },
    TOPIC_BATTERY_SOC: {
        "eos_key": BATTERY_SOC_EOS_KEY,
        "converter": lambda x: float(x) / SOC_SCALE_FACTOR,  # MQTT sends 0-100%
        "description": BATTERY_SOC_DESCRIPTION,
    },
}

MQTT_TOPICS = sorted(set(TOPIC_MAPPING.keys()) | {TOPIC_EV_DEFICIT, TOPIC_EV_PLUGGED, TOPIC_EV_MAX_POWER})

# =============================================================================
# Global State
# =============================================================================

# Track last sent values and timestamps for change detection (60 sec max)
eos_last_values: Dict[str, Optional[float]] = {}  # key -> last sent value
eos_last_timestamps: Dict[str, float] = {}  # key -> last send timestamp

# Virtual battery state
ev_deficit_wh: float = 0.0  # Energy EV needs to reach max SOC [Wh]
ev_plugged: bool = False  # Whether EV is currently plugged in
ev_max_charge_w: float = EV_MAX_CHARGE_W_DEFAULT  # EV max charge power from MQTT [W]
battery_soc_factor: Optional[float] = None  # Last known real battery SOC (0.0-1.0)
eos_current_capacity_wh: float = REAL_BATTERY_CAPACITY_WH  # Currently configured in EOS
eos_current_max_charge_w: float = REAL_BATTERY_MAX_CHARGE_W  # Currently configured in EOS

# Flag to control background threads
repeat_thread_running = False
repeat_thread: Optional[threading.Thread] = None
solution_thread: Optional[threading.Thread] = None
mqtt_client: Optional[mqtt.Client] = None  # Set in main() for publishing


# =============================================================================
# Helper Functions
# =============================================================================


def send_measurement_to_eos(
    key: str,
    value: float,
    description: str = "",
    force: bool = False,
    source: str = "",
    value_formatted: Optional[str] = None,
) -> bool:
    """Send measurement value to EOS via REST API with unified deduplication.
    
    Sends if:
    - Value changed from last sent value, OR
    - 60 seconds have passed since last send, OR
    - force=True (bypass all checks)
    
    Args:
        key: EOS measurement key
        value: Measurement value (float)
        description: Human-readable description for logging
        force: Force send even if value unchanged or time threshold not met
        source: Optional source prefix for logging (e.g., "mqtt", "repeat")
        value_formatted: Optional pre-formatted value string for logging (e.g., "85.0%", "2500W")
    
    Returns:
        True if successful or skipped, False on HTTP error
    """
    now_ts = time.time()
    now = datetime.now().astimezone().isoformat(timespec='seconds')
    
    # Check if value changed or 60 seconds passed
    last_value = eos_last_values.get(key)
    last_send_ts = eos_last_timestamps.get(key, 0)
    time_since_last_send = now_ts - last_send_ts
    
    # Determine if we should send
    should_send = force
    skip_reason = None
    
    if not should_send:
        if last_value is None:
            # First time sending this key
            should_send = True
        elif last_value != value:
            # Value changed
            logger.debug(f"Value changed: {key}: {last_value} → {value}")
            should_send = True
        elif time_since_last_send >= EOS_SEND_INTERVAL_S:
            # 60 seconds passed
            should_send = True
        else:
            skip_reason = f"unchanged, {time_since_last_send:.0f}s since last send"
    
    if not should_send:
        logger.trace(f"Skipping {key}={value} ({skip_reason})")
        return True  # Not an error, just skipped
    
    try:
        response = requests.put(
            EOS_MEASUREMENT_ENDPOINT,
            params={"datetime": now, "key": key, "value": value},
            timeout=EOS_PUT_TIMEOUT_S,
        )
        response.raise_for_status()
        
        # Update tracking on success
        eos_last_values[key] = value
        eos_last_timestamps[key] = now_ts
        
        # Unified logging with optional source prefix
        log_prefix = f"({source})" if source else ""
        log_value = value_formatted if value_formatted else f"{value:.1f}"
        logger.info(f"{log_prefix} ✓ {description}: {log_value}")
        logger.debug(f"EOS: {key}={value:.3f} → {response.status_code}")
        
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Failed to send {key}={value} to EOS: {e}")
        return False


def repeat_all_values():
    """Background thread that resends all cached values every 60 seconds."""
    logger.info("Starting value repeat timer (every 60s)...")
    while repeat_thread_running:
        try:
            time.sleep(EOS_SEND_INTERVAL_S)
            if not repeat_thread_running:
                break
            
            logger.debug("(60s timer) Resending all cached values...")
            
            # Resend BMW SOC
            bmw_key = TOPIC_MAPPING[TOPIC_BMW_SOC]["eos_key"]
            if bmw_key in eos_last_values and eos_last_values[bmw_key] is not None:
                value = eos_last_values[bmw_key]
                send_measurement_to_eos(
                    bmw_key,
                    value,
                    TOPIC_MAPPING[TOPIC_BMW_SOC]["description"],
                    force=True,
                    source="repeat",
                    value_formatted=f"{value:.1%}",
                )

            # Resend virtual battery SOC (combined)
            if battery_soc_factor is not None:
                virtual_soc = get_virtual_soc_factor()
                if virtual_soc is not None:
                    send_measurement_to_eos(
                        BATTERY_SOC_EOS_KEY,
                        virtual_soc,
                        f"Virtual Battery SOC (real={battery_soc_factor:.1%}, deficit={int(ev_deficit_wh)}Wh)",
                        force=True,
                        source="repeat",
                        value_formatted=f"{virtual_soc:.1%}",
                    )
            
        except Exception as e:
            logger.error(f"Error in repeat thread: {e}")


# =============================================================================
# Virtual Battery Logic
# =============================================================================


def get_virtual_capacity_wh() -> float:
    """Calculate virtual battery capacity = real battery + EV deficit."""
    return REAL_BATTERY_CAPACITY_WH + ev_deficit_wh


def get_virtual_soc_factor() -> Optional[float]:
    """Calculate combined virtual SOC = battery_stored / virtual_capacity."""
    if battery_soc_factor is None:
        return None
    battery_stored_wh = battery_soc_factor * REAL_BATTERY_CAPACITY_WH
    virtual_capacity = get_virtual_capacity_wh()
    return battery_stored_wh / virtual_capacity


def get_virtual_max_charge_w() -> float:
    """Max charge power: battery + EV if EV has deficit AND is plugged in."""
    if ev_deficit_wh > 0 and ev_plugged:
        return REAL_BATTERY_MAX_CHARGE_W + ev_max_charge_w
    return REAL_BATTERY_MAX_CHARGE_W


def update_eos_battery_config() -> bool:
    """Update EOS battery config with virtual capacity and charge power.

    Only sends updates if values actually changed.
    Returns True if successful (or no update needed), False on error.
    """
    global eos_current_capacity_wh, eos_current_max_charge_w

    new_capacity = int(get_virtual_capacity_wh())
    new_max_charge = int(get_virtual_max_charge_w())

    capacity_changed = abs(new_capacity - eos_current_capacity_wh) > 100  # >100 Wh threshold
    charge_changed = abs(new_max_charge - eos_current_max_charge_w) > 100

    if not capacity_changed and not charge_changed:
        return True

    config_endpoint = f"{EOS_URL}{EOS_CONFIG_PATH}"
    success = True

    if capacity_changed:
        try:
            resp = requests.put(
                f"{config_endpoint}/capacity_wh",
                json=new_capacity,
                timeout=EOS_PUT_TIMEOUT_S,
            )
            resp.raise_for_status()
            eos_current_capacity_wh = new_capacity
            logger.info(f"✓ EOS capacity updated: {new_capacity} Wh (EV deficit: {int(ev_deficit_wh)} Wh)")
        except requests.exceptions.RequestException as e:
            logger.error(f"✗ Failed to update EOS capacity: {e}")
            success = False

    if charge_changed:
        try:
            resp = requests.put(
                f"{config_endpoint}/max_charge_power_w",
                json=new_max_charge,
                timeout=EOS_PUT_TIMEOUT_S,
            )
            resp.raise_for_status()
            eos_current_max_charge_w = new_max_charge
            logger.info(f"✓ EOS max charge power updated: {new_max_charge} W")
        except requests.exceptions.RequestException as e:
            logger.error(f"✗ Failed to update EOS max charge power: {e}")
            success = False

    return success


def send_virtual_soc():
    """Calculate and send virtual combined SOC to EOS."""
    virtual_soc = get_virtual_soc_factor()
    if virtual_soc is None:
        return

    value_formatted = f"{virtual_soc:.1%}"
    send_measurement_to_eos(
        BATTERY_SOC_EOS_KEY,
        virtual_soc,
        f"Virtual Battery SOC (real={battery_soc_factor:.1%}, deficit={int(ev_deficit_wh)}Wh)",
        source="virtual",
        value_formatted=value_formatted,
    )


# =============================================================================
# EOS Solution → MQTT Publishing
# =============================================================================

# Battery operation modes that indicate charging is happening
CHARGING_MODES = {"NON_EXPORT", "GRID_SUPPORT_IMPORT", "FORCED_CHARGE", "SELF_CONSUMPTION"}
DISCHARGING_MODES = {"PEAK_SHAVING", "GRID_SUPPORT_EXPORT", "FORCED_DISCHARGE"}

# All known operation modes (prefix stripped from column names)
OPERATION_MODES = [
    "idle", "non_export", "grid_support_import", "grid_support_export",
    "peak_shaving", "self_consumption", "forced_charge", "forced_discharge",
    "fault", "frequency_regulation", "outage_supply", "ramp_rate_control",
    "reserve_backup",
]


def get_active_mode(row: dict, prefix: str = "battery1") -> tuple[str, float]:
    """Extract active operation mode and factor from a solution row.

    Returns (mode_name_upper, factor) for the first mode with op_mode == 1.0.
    Falls back to ("IDLE", 1.0) if nothing active.
    """
    for mode in OPERATION_MODES:
        mode_key = f"{prefix}_{mode}_op_mode"
        if row.get(mode_key, 0.0) == 1.0:
            factor_key = f"{prefix}_{mode}_op_factor"
            factor = row.get(factor_key, 1.0)
            return mode.upper(), factor
    return "IDLE", 1.0


def poll_eos_solution():
    """Background thread: poll EOS solution and publish current state to MQTT.

    Wakes every EOS_HEALTH_CHECK_INTERVAL_S seconds to check for a new
    optimization result via /v1/health. Publishes immediately when
    last_run_datetime changes, and also every EOS_POLL_INTERVAL_S seconds
    as a baseline refresh.
    """
    logger.info(
        f"Starting EOS solution poller (every {EOS_POLL_INTERVAL_S}s, "
        f"new optimization detected within {EOS_HEALTH_CHECK_INTERVAL_S}s)..."
    )
    last_mode = None
    last_factor = None
    last_run_datetime: Optional[str] = None
    last_publish_ts: float = 0.0

    while repeat_thread_running:
        try:
            time.sleep(EOS_HEALTH_CHECK_INTERVAL_S)
            if not repeat_thread_running:
                break
            if mqtt_client is None or not mqtt_client.is_connected():
                continue

            now_ts = time.time()
            time_since_publish = now_ts - last_publish_ts

            # Check if a new optimization has completed via health endpoint
            new_optimization = False
            try:
                health_resp = requests.get(
                    f"{EOS_URL}/v1/health",
                    timeout=EOS_PUT_TIMEOUT_S,
                )
                health_resp.raise_for_status()
                health_data = health_resp.json()
                current_last_run = health_data.get("energy-management", {}).get("last_run_datetime")
                if last_run_datetime is not None and current_last_run != last_run_datetime:
                    logger.info(f"(eos→mqtt) New optimization result detected: {current_last_run}")
                    new_optimization = True
                last_run_datetime = current_last_run
            except requests.exceptions.RequestException as e:
                logger.debug(f"Health check failed: {e}")

            # Publish if: new optimization detected OR periodic interval elapsed
            if not new_optimization and time_since_publish < EOS_POLL_INTERVAL_S:
                continue

            # Fetch solution from EOS
            try:
                resp = requests.get(
                    f"{EOS_URL}/v1/energy-management/optimization/solution",
                    timeout=EOS_PUT_TIMEOUT_S,
                )
                resp.raise_for_status()
                solution_data = resp.json()
            except requests.exceptions.RequestException as e:
                logger.warning(f"✗ Failed to poll EOS solution: {e}")
                continue

            sol = solution_data.get("solution", {}).get("data", {})
            if not sol:
                continue

            # Find current hour's row (closest timestamp <= now)
            now_dt = datetime.now().astimezone()
            sorted_timestamps = sorted(sol.keys())

            current_ts = None
            for ts in sorted_timestamps:
                if datetime.fromisoformat(ts) <= now_dt:
                    current_ts = ts
                else:
                    break

            if current_ts is None:
                current_ts = sorted_timestamps[0]

            row = sol[current_ts]

            # Extract battery operation mode
            mode, factor = get_active_mode(row, "battery1")
            charge_allowed = 1 if mode in CHARGING_MODES else 0
            discharge_allowed = 1 if mode in DISCHARGING_MODES else 0

            # Calculate charge power from planned SOC delta (current→next hour)
            current_soc = row.get("battery1_soc_factor", 0.0)
            current_idx = sorted_timestamps.index(current_ts)
            if current_idx + 1 < len(sorted_timestamps):
                next_ts = sorted_timestamps[current_idx + 1]
                next_soc = sol[next_ts].get("battery1_soc_factor", current_soc)
            else:
                next_soc = current_soc

            soc_delta = next_soc - current_soc
            # Positive delta = charging, negative = discharging
            # Energy = soc_delta * virtual_capacity; power = energy / 1h = energy in W
            planned_total_power_w = soc_delta * eos_current_capacity_wh

            if mode in CHARGING_MODES and planned_total_power_w > 0:
                # Split between real battery and EV based on capacity ratio
                battery_fraction = REAL_BATTERY_CAPACITY_WH / eos_current_capacity_wh
                battery_power_w = int(min(planned_total_power_w * battery_fraction, REAL_BATTERY_MAX_CHARGE_W))
                if ev_plugged and ev_deficit_wh > 0:
                    ev_power_w = int(min(planned_total_power_w * (1 - battery_fraction), ev_max_charge_w))
                else:
                    ev_power_w = 0
            else:
                battery_power_w = 0
                ev_power_w = 0

            # Publish current state (retained)
            mqtt_client.publish(MQTT_PUB_BATTERY_MODE, mode, retain=True)
            mqtt_client.publish(MQTT_PUB_BATTERY_FACTOR, f"{factor:.2f}", retain=True)
            mqtt_client.publish(MQTT_PUB_BATTERY_CHARGE, str(charge_allowed), retain=True)
            mqtt_client.publish(MQTT_PUB_BATTERY_DISCHARGE, str(discharge_allowed), retain=True)
            mqtt_client.publish(MQTT_PUB_BATTERY_POWER, str(battery_power_w), retain=True)
            mqtt_client.publish(MQTT_PUB_EV_CHARGE, str(charge_allowed), retain=True)
            mqtt_client.publish(MQTT_PUB_EV_POWER, str(ev_power_w), retain=True)

            # Build schedule (all hours from now onward)
            schedule = []
            for i, ts in enumerate(sorted_timestamps):
                if ts < current_ts:
                    continue
                r = sol[ts]
                m, f = get_active_mode(r, "battery1")
                # Compute planned power from SOC delta to next hour
                ts_soc = r.get("battery1_soc_factor", 0.0)
                if i + 1 < len(sorted_timestamps):
                    next_soc_s = sol[sorted_timestamps[i + 1]].get("battery1_soc_factor", ts_soc)
                else:
                    next_soc_s = ts_soc
                power_w = int((next_soc_s - ts_soc) * eos_current_capacity_wh)
                schedule.append({
                    "time": ts,
                    "mode": m,
                    "factor": round(f, 2),
                    "charge": 1 if m in CHARGING_MODES else 0,
                    "soc": round(r.get("battery1_soc_factor", 0.0), 3),
                    "power_w": power_w,
                })
            mqtt_client.publish(MQTT_PUB_SCHEDULE, json.dumps(schedule), retain=True)

            last_publish_ts = now_ts

            # Log on change
            if mode != last_mode or factor != last_factor:
                logger.info(
                    f"(eos→mqtt) Battery: {mode} @ {factor:.0%} | "
                    f"charge={charge_allowed} discharge={discharge_allowed} | "
                    f"bat={battery_power_w}W ev={ev_power_w}W (planned={int(planned_total_power_w)}W)"
                )
                last_mode = mode
                last_factor = factor

        except Exception as e:
            logger.error(f"Error in solution poller: {e}")


# =============================================================================
# MQTT Callbacks
# =============================================================================


def on_connect(client, userdata, flags, rc, properties=None):
    """Callback when MQTT connection is established."""
    if rc == 0:
        logger.success(f"✓ Connected to MQTT broker {MQTT_BROKER}:{MQTT_PORT}")

        # Subscribe to all topics
        for topic in MQTT_TOPICS:
            client.subscribe(topic)
            logger.info(f"  Subscribed to: {topic}")

    else:
        logger.error(f"✗ MQTT connection failed with code {rc}")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    """Callback when MQTT connection is lost."""
    if reason_code != 0:
        logger.warning(f"Unexpected MQTT disconnect (code {reason_code}). Reconnecting...")


def on_message(client, userdata, msg):
    """Callback when MQTT message is received."""
    global ev_deficit_wh, battery_soc_factor, ev_plugged, ev_max_charge_w

    topic = msg.topic
    payload = msg.payload.decode("utf-8")

    logger.trace(f"MQTT: {topic} = {payload}")

    try:
        # Handle EV energy deficit → update virtual battery config
        if topic == TOPIC_EV_DEFICIT:
            new_deficit = max(0.0, float(payload))
            if abs(new_deficit - ev_deficit_wh) > 100:  # >100 Wh change threshold
                ev_deficit_wh = new_deficit
                logger.info(f"(mqtt) ✓ EV deficit: {int(ev_deficit_wh)} Wh")
                update_eos_battery_config()
                send_virtual_soc()

        # Handle EV plugged state → update max charge power
        elif topic == TOPIC_EV_PLUGGED:
            new_plugged = int(float(payload)) == 1
            if new_plugged != ev_plugged:
                ev_plugged = new_plugged
                logger.info(f"(mqtt) ✓ EV plugged: {ev_plugged}")
                update_eos_battery_config()

        # Handle EV max charge power
        elif topic == TOPIC_EV_MAX_POWER:
            new_max_w = max(0.0, float(payload))
            if abs(new_max_w - ev_max_charge_w) > 100:
                ev_max_charge_w = new_max_w
                logger.info(f"(mqtt) ✓ EV max charge power: {int(ev_max_charge_w)} W")
                update_eos_battery_config()

        # Handle battery SOC → store real value and send virtual SOC
        elif topic == TOPIC_BATTERY_SOC:
            config = TOPIC_MAPPING[topic]
            raw_value = float(payload)
            battery_soc_factor = config["converter"](raw_value)
            # Send virtual combined SOC (not raw battery SOC)
            send_virtual_soc()

        # Handle other direct mapped topics (BMW SOC)
        elif topic in TOPIC_MAPPING:
            config = TOPIC_MAPPING[topic]
            raw_value = float(payload)
            converted_value = config["converter"](raw_value)

            value_formatted = f"{converted_value:.1%}"
            send_measurement_to_eos(
                config["eos_key"],
                converted_value,
                config["description"],
                source="mqtt",
                value_formatted=value_formatted,
            )

        else:
            logger.warning(f"Unknown topic: {topic}")

    except ValueError as e:
        logger.error(f"Invalid value for topic {topic}: {payload} - {e}")
    except Exception as e:
        logger.exception(f"Error processing message from {topic}: {e}")


# =============================================================================
# Main
# =============================================================================


def main():
    """Main entry point."""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        level=LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )

    logger.info("=" * 70)
    logger.info("MQTT → EOS Bridge")
    logger.info("=" * 70)
    logger.info(f"MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}")
    logger.info(f"MQTT User: {MQTT_USER}")
    logger.info(f"EOS URL: {EOS_URL}")
    logger.info(f"Log Level: {LOG_LEVEL}")
    logger.info("=" * 70)

    # Check EOS connectivity
    try:
        response = requests.get(f"{EOS_URL}{EOS_HEALTH_PATH}", timeout=EOS_HEALTH_TIMEOUT_S)
        response.raise_for_status()
        logger.success(f"✓ EOS is reachable at {EOS_URL}")
    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Cannot reach EOS at {EOS_URL}: {e}")
        logger.error("  Make sure EOS server is running.")
        sys.exit(1)

    # Create MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    # Set callbacks
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # Connect to broker
    try:
        logger.info(f"Connecting to MQTT broker {MQTT_BROKER}:{MQTT_PORT}...")
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=MQTT_KEEPALIVE_S)
    except Exception as e:
        logger.error(f"✗ Failed to connect to MQTT broker: {e}")
        sys.exit(1)

    # Start background repeat thread
    global repeat_thread_running, repeat_thread, solution_thread, mqtt_client
    mqtt_client = client
    repeat_thread_running = True
    repeat_thread = threading.Thread(target=repeat_all_values, daemon=True)
    repeat_thread.start()

    # Start EOS solution polling thread
    solution_thread = threading.Thread(target=poll_eos_solution, daemon=True)
    solution_thread.start()
    
    # Start MQTT loop
    logger.info("Starting MQTT loop... (Press Ctrl+C to exit)")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        repeat_thread_running = False
        client.disconnect()
        if repeat_thread:
            repeat_thread.join(timeout=2)
        if solution_thread:
            solution_thread.join(timeout=2)
        logger.success("Bridge stopped.")


if __name__ == "__main__":
    main()
