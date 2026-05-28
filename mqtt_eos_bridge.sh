#!/bin/bash
ABSPATH=$(readlink -f $0)
ABSDIR=$(dirname $ABSPATH)

cd $ABSDIR
export MQTT_PASSWORD=$(cat .mqtt_password)
uv run python scripts/mqtt_eos_bridge.py
