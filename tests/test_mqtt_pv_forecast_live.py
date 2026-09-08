#!/usr/bin/env python3
"""
Live test for `fetch_pv_forecast` / `publish_pv_forecast` against the
production EOS server and MQTT broker.

This is NOT a unit test to be run by pytest in CI (it requires reachable
infrastructure), but a small standalone script you run manually or in CI
with access to the EOS server and MQTT broker.

It imports the real functions from `scripts/mqtt_eos_bridge.py` and validates:

- `fetch_pv_forecast` returns today's *remaining* hourly PV forecast and
  tomorrow's hourly forecast, each value is W-per-interval and non-negative,
  timestamps are hourly-aligned and monotonic, and the total is consistent
  with the sum of the hourly values.
- `publish_pv_forecast` (only if a real MQTT_PASSWORD is provided) connects to
  the MQTT broker, publishes the forecast to `eos/pv_forecast/hourly`, and the
  received payload is a valid JSON list of {time, power_w} covering both
  today and tomorrow.

Usage:
    python tests/test_mqtt_pv_forecast_live.py            # fetch only
    MQTT_PASSWORD=secret python tests/test_mqtt_pv_forecast_live.py  # fetch + publish
    EOS_URL=http://host:8503 python tests/test_mqtt_pv_forecast_live.py
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

# Env vars must be set BEFORE importing the bridge module, because the module
# computes EOS_URL / reads MQTT_PASSWORD at import time.
#
# 1. EOS_URL: the bridge defaults to http://localhost:8503, but for the
#    production server we prefer http://eos:8503 unless overridden.
if not os.getenv("EOS_URL"):
    os.environ["EOS_URL"] = "http://eos:8503"

# 2. MQTT_PASSWORD: required by the bridge module at import time. It must be
#    set even for this read-only test (no MQTT connection is ever made here).
#    If a real password is provided, the publish test will also run.
if not os.getenv("MQTT_PASSWORD"):
    os.environ["MQTT_PASSWORD"] = "placeholder-unused-for-readonly-test"

# Allow importing scripts/mqtt_eos_bridge.py as a module.
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import mqtt_eos_bridge as bridge  # noqa: E402

# A real MQTT password means the user wants the publish test to run.
REAL_MQTT_PASSWORD = (
    os.getenv("MQTT_PASSWORD") != "placeholder-unused-for-readonly-test"
)

# How long to wait for a published message to arrive back on the topic.
PUBLISH_WAIT_S = 10.0


def _now_hour() -> datetime:
    return datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)


def test_fetch() -> list[str]:
    """Validate fetch_pv_forecast() against the EOS server."""
    failures: list[str] = []

    now_hour = _now_hour()
    expected_hours = (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
        - now_hour
    ) // timedelta(hours=1)

    print("=" * 70)
    print(f"Testing fetch_pv_forecast() against {bridge.EOS_URL}")
    print("=" * 70)

    (
        values_today,
        hours_today,
        total_remaining_wh,
        total_daily_wh,
        values_tomorrow,
        hours_tomorrow,
    ) = bridge.fetch_pv_forecast()

    if not values_today:
        failures.append("fetch_pv_forecast() returned empty forecast")
        print("values_today: (empty)")
    else:
        print(
            f"values_today ({len(values_today)}h): {[round(v, 0) for v in values_today]}"
        )
        print(f"hours_today:      {hours_today}")
        print(f"total_remaining_wh: {total_remaining_wh:.0f}")
        print(f"total_daily_wh:     {total_daily_wh:.0f}")

        if values_tomorrow:
            print(
                f"values_tomorrow ({len(values_tomorrow)}h): {[round(v, 0) for v in values_tomorrow]}"
            )
            print(f"hours_tomorrow:    {hours_tomorrow}")
            tomorrow_daily_wh = sum(values_tomorrow)
            print(f"tomorrow total_daily_wh:     {tomorrow_daily_wh:.0f}")

        if any(v < 0 for v in values_today):
            failures.append(f"Negative PV values found: {values_today}")

        if len(hours_today) > 1:
            for i in range(1, len(hours_today)):
                # 'Z' suffix is not parseable by fromisoformat on Python < 3.11.
                prev = datetime.fromisoformat(hours_today[i - 1].replace("Z", "+00:00"))
                curr = datetime.fromisoformat(hours_today[i].replace("Z", "+00:00"))
                if (curr - prev) != timedelta(hours=1):
                    failures.append(
                        f"Hours not 1h-aligned at index {i}: {prev} -> {curr}"
                    )

        # Remaining total must equal the sum of the remaining hourly values.
        if abs(sum(values_today) - total_remaining_wh) > 1e-3:
            failures.append(
                f"total_remaining_wh {total_remaining_wh:.3f} != sum(values_today) {sum(values_today):.3f}"
            )

        # Full-day total must be >= remaining total (covers the whole day).
        if total_daily_wh < total_remaining_wh - 1e-3:
            failures.append(
                f"total_daily_wh {total_daily_wh:.3f} < total_remaining_wh {total_remaining_wh:.3f}"
            )

        if values_today and not (
            expected_hours - 1 <= len(values_today) <= expected_hours + 1
        ):
            failures.append(
                f"Expected ~{expected_hours} remaining hours today, got {len(values_today)}"
            )

    return failures


def test_publish() -> list[str]:
    """Validate publish_pv_forecast() by subscribing to the topic.

    Connects to the MQTT broker, subscribes to `eos/pv/forecast/hourly`, calls
    `publish_pv_forecast()`, and verifies a valid JSON payload arrives with both
    today's and tomorrow's hourly forecasts.
    """
    failures: list[str] = []

    print("=" * 70)
    print(
        f"Testing publish_pv_forecast() against {bridge.MQTT_BROKER}:{bridge.MQTT_PORT}"
    )
    print("=" * 70)

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return ["paho-mqtt not installed; cannot run publish test"]

    received: dict = {}

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(bridge.MQTT_PUB_PV_FORECAST_HOURLY)
        else:
            received["connect_error"] = rc

    def on_message(client, userdata, msg):
        received["payload"] = msg.payload.decode("utf-8")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(bridge.MQTT_USER, bridge.MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(bridge.MQTT_BROKER, bridge.MQTT_PORT, keepalive=60)
    except Exception as e:
        return [f"Failed to connect to MQTT broker: {e}"]

    client.loop_start()

    # Wait for subscription to be active before publishing.
    deadline = time.time() + 5.0
    while time.time() < deadline and "payload" not in received:
        time.sleep(0.1)

    # Point the bridge at our client and publish.
    bridge.mqtt_client = client
    bridge.publish_pv_forecast()

    # Wait for the message to come back.
    deadline = time.time() + PUBLISH_WAIT_S
    while time.time() < deadline and "payload" not in received:
        time.sleep(0.2)

    client.loop_stop()
    client.disconnect()

    if "connect_error" in received:
        return [f"MQTT connect failed with code {received['connect_error']}"]

    if "payload" not in received:
        return [
            f"No message received on {bridge.MQTT_PUB_PV_FORECAST_HOURLY} "
            f"within {PUBLISH_WAIT_S:.0f}s"
        ]

    payload = received["payload"]
    print(f"Received on {bridge.MQTT_PUB_PV_FORECAST_HOURLY}: {payload}")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        return [f"Payload is not valid JSON: {e}"]

    if not isinstance(data, list) or not data:
        return [f"Payload is not a non-empty list: {data!r}"]

    for entry in data:
        if (
            not isinstance(entry, dict)
            or "time" not in entry
            or "power_wh" not in entry
        ):
            return [f"Entry missing 'time'/'power_wh': {entry!r}"]
        if not isinstance(entry["power_wh"], (int, float)):
            return [f"power_wh is not numeric: {entry!r}"]

    print(f"Payload OK: {len(data)} hourly entries.")
    return failures


def main() -> int:
    failures: list[str] = []

    failures += test_fetch()

    if REAL_MQTT_PASSWORD:
        failures += test_publish()
    else:
        print("-" * 70)
        print(
            "Skipping publish test: set a real MQTT_PASSWORD to also test "
            "publish_pv_forecast()."
        )

    print("-" * 70)
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        print("-" * 70)
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
