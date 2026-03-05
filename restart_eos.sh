#!/bin/bash

# Script to restart EOS and MQTT bridge with health checks

set -e

VENV="/home/arne/projects/eos/.venv/bin"
EOS_PID_FILE="/tmp/eos.pid"
MQTT_PID_FILE="/tmp/mqtt_bridge.pid"
EOS_PORT="8503"
MQTT_HOST="127.0.0.1"
MQTT_PORT="1880"
MQTT_PASSWORD_FILE="/home/arne/projects/eos/.mqtt_password"
MAX_RETRIES=200
RETRY_INTERVAL=1

echo "🛑 Stopping MQTT bridge..."
if pgrep -f "mqtt_eos_bridge" > /dev/null; then
    pkill -f "mqtt_eos_bridge" || true
    sleep 2
    # Force kill if still running
    if pgrep -f "mqtt_eos_bridge" > /dev/null; then
        echo "   Force killing MQTT bridge..."
        pkill -9 -f "mqtt_eos_bridge" || true
        sleep 1
    fi
fi

echo "🛑 Stopping EOS server..."
if pgrep -f "akkudoktoreos.server.eos" > /dev/null; then
    pkill -f "akkudoktoreos.server.eos" || true
    sleep 3
    # Force kill if still running
    if pgrep -f "akkudoktoreos.server.eos" > /dev/null; then
        echo "   Force killing EOS server..."
        pkill -9 -f "akkudoktoreos.server.eos" || true
        sleep 1
    fi
fi

# Wait for port to be free
echo "⏳ Waiting for port $EOS_PORT to be free..."
WAIT_COUNT=0
while lsof -i :$EOS_PORT > /dev/null 2>&1; do
    WAIT_COUNT=$((WAIT_COUNT + 1))
    if [ $WAIT_COUNT -gt 10 ]; then
        echo "❌ Port $EOS_PORT still in use after 10 seconds"
        lsof -i :$EOS_PORT
        exit 1
    fi
    sleep 1
done

echo "🚀 Starting EOS server..."
cd /home/arne/projects/eos
$VENV/python -m akkudoktoreos.server.eos > /tmp/eos_server.log 2>&1 &
EOS_PID=$!
echo $EOS_PID > $EOS_PID_FILE
echo "   EOS PID: $EOS_PID"

echo "⏳ Waiting for EOS to be ready (checking http://127.0.0.1:$EOS_PORT/api/health)..."
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -sS "http://127.0.0.1:$EOS_PORT/api/health" > /dev/null 2>&1; then
        echo "✅ EOS is ready!"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $((RETRY_COUNT % 10)) -eq 0 ]; then
        echo "   Waiting... (attempt $RETRY_COUNT/$MAX_RETRIES)"
    fi
    sleep $RETRY_INTERVAL
done

if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
    echo "❌ EOS failed to start within ${MAX_RETRIES}s"
    echo ""
    echo "Last 30 lines of log:"
    tail -30 /tmp/eos_server.log
    echo ""
    echo "Checking if process is still running..."
    if ps -p $EOS_PID > /dev/null 2>&1; then
        echo "⚠️  Process is running but not responding to HTTP requests"
    else
        echo "❌ Process is not running (crashed)"
    fi
    exit 1
fi

# Verify EOS process is still running
if ! ps -p $EOS_PID > /dev/null 2>&1; then
    echo "❌ EOS process died after starting (PID $EOS_PID)"
    tail -30 /tmp/eos_server.log
    exit 1
fi


# Verify EOS process is still running after optimization wait
if ! ps -p $EOS_PID > /dev/null 2>&1; then
    echo "❌ EOS process died during optimization (PID $EOS_PID)"
    tail -30 /tmp/eos_server.log
    exit 1
fi

echo "🚀 Starting MQTT bridge..."
if [ ! -f "$MQTT_PASSWORD_FILE" ]; then
    echo "❌ MQTT password file not found: $MQTT_PASSWORD_FILE"
    echo "   Create it with: printf '%s\n' 'your-password' > $MQTT_PASSWORD_FILE"
    exit 1
fi

MQTT_PASSWORD=$(tr -d '\r\n' < "$MQTT_PASSWORD_FILE")
if [ -z "$MQTT_PASSWORD" ]; then
    echo "❌ MQTT password file is empty: $MQTT_PASSWORD_FILE"
    exit 1
fi
export MQTT_PASSWORD

cd /home/arne/projects/eos
$VENV/python scripts/mqtt_eos_bridge.py > /tmp/mqtt_bridge.log 2>&1 &
MQTT_PID=$!
echo $MQTT_PID > $MQTT_PID_FILE
echo "   MQTT Bridge PID: $MQTT_PID"

sleep 3

# Verify MQTT bridge is still running
if ! ps -p $MQTT_PID > /dev/null 2>&1; then
    echo "⚠️  MQTT bridge process died after starting"
    echo "Last 20 lines of MQTT log:"
    tail -20 /tmp/mqtt_bridge.log 2>/dev/null || echo "(no log available)"
    # Don't exit - EOS is still running
fi



echo "⏳ Waiting for optimization solution to be available..."
RETRY_COUNT=0
MAX_OPT_RETRIES=120  # 2 minutes for optimization to complete
while [ $RETRY_COUNT -lt $MAX_OPT_RETRIES ]; do
    HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:$EOS_PORT/v1/energy-management/optimization/solution" 2>&1)
    if [ "$HTTP_CODE" = "200" ]; then
        echo "✅ Optimization solution is available!"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $((RETRY_COUNT % 15)) -eq 0 ]; then
        echo "   Waiting for optimization... (attempt $RETRY_COUNT/$MAX_OPT_RETRIES, HTTP $HTTP_CODE)"
    fi
    sleep 1
done

if [ $RETRY_COUNT -ge $MAX_OPT_RETRIES ]; then
    echo "⚠️  Optimization solution not available after ${MAX_OPT_RETRIES}s (still HTTP 404)"
    exit 1
fi

echo ""
echo "✅ All services started successfully!"
echo "   EOS:         http://127.0.0.1:$EOS_PORT"
echo "   MQTT Bridge: running (PID $MQTT_PID)"
echo ""
echo "📋 Logs:"
echo "   EOS:         tail -f /tmp/eos_server.log"
echo "   MQTT Bridge: tail -f /tmp/mqtt_bridge.log"
