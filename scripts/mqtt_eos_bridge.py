#!/usr/bin/env python3
"""
MQTT to EOS Bridge
==================

Connects to MQTT broker and forwards device measurements to EOS REST API.

MQTT Topics:
- devices/bmw_i5/cardata/drivetrain/batteryManagement/header → BMW_i5-soc-factor
- devices/victron_battery/battery_soc → LiFePO4_Cluster-soc-factor
- devices/victron_battery/ac_power_w + devices/victron_battery_2/ac_power_w → LiFePO4_Cluster-power-3-phase-sym-w

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

import os
import sys
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
EOS_HEALTH_PATH = "/docs"

EOS_PUT_TIMEOUT_S = 5
EOS_HEALTH_TIMEOUT_S = 5
EOS_SEND_INTERVAL_S = 60
BATTERY_POWER_DEBOUNCE_S = 5
MQTT_KEEPALIVE_S = 60

TOPIC_BMW_SOC = "devices/bmw_i5/cardata/drivetrain/batteryManagement/header"
TOPIC_BATTERY_SOC = "devices/victron_battery/battery_soc"
TOPIC_BATTERY_POWER_1 = "devices/victron_battery/ac_power_w"
TOPIC_BATTERY_POWER_2 = "devices/victron_battery_2/ac_power_w"

BMW_SOC_EOS_KEY = "BMW_i5-soc-factor"
BATTERY_SOC_EOS_KEY = "LiFePO4_Cluster-soc-factor"
BATTERY_POWER_EOS_KEY = "LiFePO4_Cluster-power-3-phase-sym-w"

BMW_SOC_DESCRIPTION = "BMW i5 State of Charge"
BATTERY_SOC_DESCRIPTION = "Battery State of Charge"

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

# Battery power requires summing two topics
BATTERY_POWER_TOPICS = [
    TOPIC_BATTERY_POWER_1,
    TOPIC_BATTERY_POWER_2,
]

MQTT_TOPICS = sorted(set(TOPIC_MAPPING.keys()) | set(BATTERY_POWER_TOPICS))

# =============================================================================
# Global State
# =============================================================================

battery_power_cache: Dict[str, Optional[float]] = {
    TOPIC_BATTERY_POWER_1: None,
    TOPIC_BATTERY_POWER_2: None,
}
battery_power_last_update = 0.0

# Track last sent values and timestamps for change detection (60 sec max)
eos_last_values: Dict[str, Optional[float]] = {}  # key -> last sent value
eos_last_timestamps: Dict[str, float] = {}  # key -> last send timestamp


# =============================================================================
# Helper Functions
# =============================================================================


def send_to_eos(key: str, value: float, description: str = "") -> bool:
    """Send measurement value to EOS via REST API.
    
    Only sends if:
    - Value changed from last sent value, OR
    - 60 seconds have passed since last send

    Args:
        key: EOS measurement key
        value: Measurement value
        description: Optional description for logging

    Returns:
        True if successful, False otherwise
    """
    now_ts = time.time()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]  # ISO 8601 with milliseconds
    
    # Check if value changed or 60 seconds passed
    last_value = eos_last_values.get(key)
    last_send_ts = eos_last_timestamps.get(key, 0)
    time_since_last_send = now_ts - last_send_ts
    
    # Send if: value changed OR 60+ seconds passed
    if last_value != value and last_value is not None:
        logger.debug(f"Value changed: {key}: {last_value} → {value}")
    elif time_since_last_send < EOS_SEND_INTERVAL_S:
        # Same value and less than 60 seconds - skip
        logger.trace(f"Skipping {key}={value} (unchanged, {time_since_last_send:.0f}s since last send)")
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
        
        logger.debug(f"✓ EOS: {key}={value:.3f} ({description}) → {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Failed to send {key}={value} to EOS: {e}")
        return False


def process_battery_power():
    """Process battery power by summing both Victron battery readings."""
    global battery_power_last_update

    # Check if we have both values
    if None in battery_power_cache.values():
        logger.debug(
            f"Battery power: waiting for both values (victron: {battery_power_cache[TOPIC_BATTERY_POWER_1]}, "
            f"victron2: {battery_power_cache[TOPIC_BATTERY_POWER_2]})"
        )
        return

    # Sum power values (negative=discharge, positive=charge)
    total_power = sum(battery_power_cache.values())

    # Avoid sending duplicate values too frequently (debouncing)
    now = time.time()
    if now - battery_power_last_update < BATTERY_POWER_DEBOUNCE_S:  # Min seconds between updates
        return

    battery_power_last_update = now

    # Send to EOS
    success = send_to_eos(
        BATTERY_POWER_EOS_KEY,
        total_power,
        f"Battery Power (victron:{battery_power_cache[TOPIC_BATTERY_POWER_1]:.1f}W + "
        f"victron2:{battery_power_cache[TOPIC_BATTERY_POWER_2]:.1f}W)",
    )

    if success:
        logger.info(
            f"Battery Power: {total_power:.1f}W "
            f"({'charging' if total_power > 0 else 'discharging' if total_power < 0 else 'idle'})"
        )


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


def on_disconnect(client, userdata, rc, properties=None):
    """Callback when MQTT connection is lost."""
    if rc != 0:
        logger.warning(f"Unexpected MQTT disconnect (code {rc}). Reconnecting...")


def on_message(client, userdata, msg):
    """Callback when MQTT message is received."""
    topic = msg.topic
    payload = msg.payload.decode("utf-8")

    logger.trace(f"MQTT: {topic} = {payload}")

    try:
        # Handle direct mapped topics (SOC values)
        if topic in TOPIC_MAPPING:
            config = TOPIC_MAPPING[topic]
            raw_value = float(payload)
            converted_value = config["converter"](raw_value)

            send_to_eos(config["eos_key"], converted_value, config["description"])

        # Handle battery power topics (need to sum two values)
        elif topic in battery_power_cache:
            battery_power_cache[topic] = float(payload)
            process_battery_power()

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

    # Start MQTT loop
    logger.info("Starting MQTT loop... (Press Ctrl+C to exit)")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        client.disconnect()
        logger.success("Bridge stopped.")


if __name__ == "__main__":
    main()
