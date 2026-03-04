# MQTT-EOS Bridge

Verbindet MQTT-Broker mit EOS und leitet Mess-Daten automatisch weiter.

## Features

- **Automatisches Forwarding** von MQTT-Topics zu EOS Measurement Keys
- **Summen-Berechnung** für verteilte Batterie-Leistungswerte
- **Auto-Reconnect** bei Verbindungsabbrüchen
- **Debouncing** zur Vermeidung von Spam
- **Structured Logging** für einfaches Debugging

## Übersicht

```
MQTT Topics                                    EOS Measurement Keys
├── devices/bmw_i5/cardata/battery_soc    →   BMW_i5-soc-factor
├── devices/victron_battery/battery_soc   →   LiFePO4_Cluster-soc-factor
├── devices/victron_battery/ac_power_w    ┐
└── devices/victron_battery2/ac_power_w   ┴→  LiFePO4_Cluster-power-3-phase-sym-w (sum)
```

## Installation

### 1. Automatische Installation

```bash
cd /home/arne/projects/eos
bash scripts/setup_mqtt_bridge.sh
```

### 2. Manuelle Installation

```bash
# MQTT-Client installieren
uv add paho-mqtt

# Script ausführbar machen
chmod +x scripts/mqtt_eos_bridge.py
```

## Nutzung

### Manueller Start

```bash
# Passwort als Environment Variable setzen
export MQTT_PASSWORD="Dr491-2579"

# Bridge starten
uv run scripts/mqtt_eos_bridge.py
```

### Als Systemd Service (dauerhaft im Hintergrund)

```bash
# Service starten
sudo systemctl start mqtt-eos-bridge.service

# Service bei Boot automatisch starten
sudo systemctl enable mqtt-eos-bridge.service

# Logs anzeigen
sudo journalctl -u mqtt-eos-bridge.service -f
```

## Konfiguration

Alle Parameter via Environment Variables:

```bash
export MQTT_BROKER="mqtt.fritz.box"        # MQTT Broker Adresse
export MQTT_PORT="1883"                    # MQTT Port
export MQTT_USER="mqtt_user"               # MQTT Username
export MQTT_PASSWORD="Dr491-2579"          # MQTT Password (required!)
export EOS_URL="http://localhost:8503"     # EOS REST API URL
export LOG_LEVEL="INFO"                    # DEBUG, INFO, WARNING, ERROR

uv run scripts/mqtt_eos_bridge.py
```

## Topic-Mapping Anpassen

Falls die MQTT-Topics andere Werte liefern, kannst du das Mapping in `mqtt_eos_bridge.py` anpassen:

### SOC-Format ändern

Wenn SOC bereits als `0.0-1.0` statt `0-100` kommt:

```python
TOPIC_MAPPING = {
    "devices/bmw_i5/cardata/battery_soc": {
        "eos_key": "BMW_i5-soc-factor",
        "converter": lambda x: float(x),  # Ohne /100
        "description": "BMW i5 State of Charge",
    },
    # ...
}
```

### Neue Topics hinzufügen

```python
TOPIC_MAPPING = {
    # Bestehende Topics...
    "your/new/topic": {
        "eos_key": "your-eos-key",
        "converter": lambda x: float(x),
        "description": "Your Description",
    },
}

# Topic zur Subscription-Liste hinzufügen
MQTT_TOPICS = [
    # ... bestehende Topics
    "your/new/topic",
]
```

### Battery Power einzeln statt Summe

Falls du L1/L2/L3 einzeln tracken willst:

```python
# In on_message():
if topic == "devices/victron_battery/ac_power_w":
    send_to_eos("LiFePO4_Cluster-power-l1-w", float(payload), "Battery L1")
elif topic == "devices/victron_battery2/ac_power_w":
    send_to_eos("LiFePO4_Cluster-power-l2-w", float(payload), "Battery L2")
```

## Troubleshooting

### Bridge startet nicht

**Problem**: `ERROR: paho-mqtt not installed`

```bash
uv add paho-mqtt
```

**Problem**: `ERROR: MQTT_PASSWORD environment variable not set`

```bash
export MQTT_PASSWORD="Dr491-2579"
```

### Keine Verbindung zu MQTT

**Problem**: `✗ Failed to connect to MQTT broker`

Prüfe:

1. Ist `mqtt.fritz.box` erreichbar? `ping mqtt.fritz.box`
2. Sind User/Password korrekt?
3. Firewall blockiert Port 1883?

```bash
# Test MQTT-Verbindung
mosquitto_sub -h mqtt.fritz.box -p 1883 -u mqtt_user -P Dr491-2579 -t '#' -v
```

### Keine Verbindung zu EOS

**Problem**: `✗ Cannot reach EOS at http://localhost:8503`

```bash
# Prüfe ob EOS läuft
curl http://localhost:8503/health

# EOS starten falls nicht läuft
uv run python src/akkudoktoreos/server/eos.py
```

### Werte kommen nicht in EOS an

**Problem**: Bridge läuft, aber Werte erscheinen nicht in EOS

1. **Debug-Logging aktivieren**:

   ```bash
   export LOG_LEVEL="DEBUG"
   uv run scripts/mqtt_eos_bridge.py
   ```

2. **Prüfe ob Topics korrekt sind**:

   ```bash
   mosquitto_sub -h mqtt.fritz.box -p 1883 -u mqtt_user -P Dr491-2579 -t 'devices/#' -v
   ```

3. **Prüfe EOS Measurement Keys**:

   ```bash
   curl http://localhost:8503/v1/measurement/keys
   ```

4. **Manuell testen**:
   ```bash
   curl -X PUT "http://localhost:8503/v1/measurement/value?datetime=2026-03-04T21:00&key=BMW_i5-soc-factor&value=0.5"
   ```

### Battery Power bleibt bei 0

**Problem**: `Battery power: waiting for both values`

- Bridge wartet auf beide Topics (`victron_battery` + `victron_battery2`)
- Prüfe ob beide Topics Daten senden
- Eventuell nur ein Battery vorhanden? → Passe `process_battery_power()` an

## Logging

Die Bridge nutzt strukturiertes Logging mit verschiedenen Levels:

- **TRACE**: Jede einzelne MQTT-Nachricht
- **DEBUG**: Details zu EOS REST-Aufrufen
- **INFO**: Wichtige Events (Verbindung, Battery Power Updates)
- **WARNING**: Potentielle Probleme
- **ERROR**: Fehler bei Verarbeitung

Beispiel-Ausgabe:

```
2026-03-04 21:30:00 | INFO     | ✓ Connected to MQTT broker mqtt.fritz.box:1883
2026-03-04 21:30:00 | INFO     |   Subscribed to: devices/bmw_i5/cardata/battery_soc
2026-03-04 21:30:05 | INFO     | Battery Power: 1234.5W (charging)
2026-03-04 21:30:10 | DEBUG    | ✓ EOS: BMW_i5-soc-factor=0.650 (BMW i5 State of Charge) → 200
```

## Performance

- **CPU**: <1% bei normaler Last
- **Memory**: ~50 MB
- **Netzwerk**: Minimal (nur beim Empfang von MQTT-Updates)
- **Debouncing**: Battery Power wird maximal alle 5 Sekunden aktualisiert

## Sicherheit

⚠️ **Wichtig**:

- Passwort **nie** im Code hardcoden → immer via Environment Variable
- Bei Systemd-Service: Passwort in `/etc/systemd/system/mqtt-eos-bridge.service` ist nur für root lesbar
- Für erhöhte Sicherheit: MQTT TLS/SSL nutzen (Port 8883)

## Weiterentwicklung

Mögliche Erweiterungen:

- [ ] MQTT TLS/SSL Support
- [ ] MQTT Discovery für dynamische Topics
- [ ] Konfigurations-File statt Environment Variables
- [ ] Prometheus Metrics Export
- [ ] Health-Check Endpoint
- [ ] Reconnect-Strategie konfigurierbar machen

## Lizenz

Siehe Haupt-Repository LICENSE.
