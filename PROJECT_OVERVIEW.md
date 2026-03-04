# AkkudoktorEOS – Project Overview

**Generated**: 2026-03-04

## 📋 Projekt-Zusammenfassung

**AkkudoktorEOS** ist ein Python-basiertes Energiemanagementsystem zur Simulation und Optimierung von Haushalts-Energieflüssen:

- 🔋 Batteriespeicher-Optimierung
- ☀️ PV-Anlagen & Solarprognosen
- ⚡ Lastmanagement & Stromverbrauch
- 🚗 Elektrofahrzeuge & Wärmepumpen
- 💰 Strompreisoptimierung
- 🧠 Prognose-basierte Optimierung mit genetischen Algorithmen

Das System ist speziell als **Home Assistant Add-on** konzipiert und integrierbar mit **NodeRED** und **EVCC**.

---

## 🏗️ Repository-Struktur

### Hauptverzeichnisse

```
src/akkudoktoreos/        # Hauptquellcode
├── adapter/              # Schnittstellen zu externen Systemen
├── config/               # Konfiguration & Validierung (Pydantic)
├── core/                 # Kern-Logik & Datenstrukturen
├── data/                 # Datenverwaltung & Caching (LMDB)
├── devices/              # Modelle für Geräte (Batterie, PV, etc.)
├── measurement/          # Messdaten & Metriken
├── optimization/         # Genetische Algorithmen (DEAP), Optimierung
├── prediction/           # Vorhersagen (Wetter, PV, Strompreise)
├── server/               # FastAPI Server & EOSDash Dashboard
└── utils/                # Utility-Funktionen

tests/                    # 38+ Test-Dateien (pytest)
docs/                     # Sphinx-Dokumentation
scripts/                  # Hilfsskripte (Versionierung, Builds, etc.)
```

### Konfigurationsdateien

| Datei                                      | Zweck                                            |
| ------------------------------------------ | ------------------------------------------------ |
| [pyproject.toml](pyproject.toml)           | Dependencies, Projektmetadaten, uv-Konfiguration |
| [Dockerfile](Dockerfile)                   | Docker-Image für Production                      |
| [build.yaml](build.yaml)                   | Home Assistant Add-on Build-Konfiguration        |
| [docker-compose.yaml](docker-compose.yaml) | Lokale Entwicklung (Services, DB)                |
| [Makefile](Makefile)                       | Build-Targets & Entwickler-Befehle               |
| [config.yaml](config.yaml)                 | Home Assistant Add-on Konfiguration              |

---

## 🔧 Tech-Stack

### Sprache & Runtime

- **Python**: 3.11+ (aktuell 3.13 im Dockerfile)
- **Paketmanager**: `uv` (schnell & zuverlässig)

### Web & Server

- **Framework**: FastAPI ≥ 0.135.1
- **Server**: Uvicorn 0.41.0
- **UI-Framework**: FastHTML 0.12.48, Bokeh 3.8.2, MonsterUI 1.0.44

### Datenverarbeitung & Wissenschaft

- **NumPy**: 2.4.2
- **Pandas**: 3.0.1
- **SciPy**: 1.17.1
- **Matplotlib**: 3.10.8
- **statsmodels**: 0.14.6 (Zeitreihenanalyse)

### Datenbanken & Caching

- **LMDB**: 1.7.5 (Key-Value Store)
- **Cachebox**: 5.2.2

### Optimierung & Simulation

- **DEAP**: 1.4.3 (genetische Algorithmen)
- **Pydantic**: 2.12.5 (Datenvalidierung)
- **Numpydantic**: 1.8.0 (NumPy + Pydantic Integration)

### Externe APIs & Daten

- **Requests**: 2.32.5
- **pvlib**: 0.15.0 (PV-Simulationen)
- **tzfpy**: 1.1.1 (Zeitzonen)
- **Pendulum**: 3.2.0 (Datum/Zeit-Handling)

### Logging & Dokumentation

- **Loguru**: 0.7.3
- **Sphinx**: 9.0.4 (Doku-Generator)

---

## 📊 Testing & Qualität

### Test-Coverage

- **38+ Test-Dateien** in [tests/](tests/)
- pytest als Test-Framework
- Einzelne Test-Dateien für jedes Modul
- [conftest.py](tests/conftest.py) mit gemeinsamen Fixtures

### Qualitätssicherung

- **MyPy**: Statische Typ-Überprüfung
- **pre-commit**: Automatische Checks vor Commits
- **GitLint**: Commit-Message Validierung
- **Commitizen**: Konventionelle Commits (cz)
- **Ruff**: Code-Formatter & Linter

### CI/CD

- Automatisierte Builds über Commitizen
- Docker-Image für amd64 & aarch64 (ARM)

---

## 🚀 Häufige Entwickler-Befehle

### Setup & Installation

```bash
make install          # Installation im Dev-Modus mit allen Dependencies
make update-env       # Virtuelle Umgebung aktualisieren (zu pyproject.toml sync)
uv sync               # Alle Dependencies installieren
```

### Entwicklung

```bash
make run-dev          # EOS Development-Server (Auto-Reload auf Port 8503)
make run-dash-dev     # EOSDash Dashboard im Dev-Modus (Auto-Reload auf Port 8504)
make format           # Code formatieren (isort, ruff)
```

### Testing

```bash
make test             # Alle Tests ausführen
make test-system      # Tests inkl. System-Tests
make test-ci          # CI-Tests
make mypy             # Type-Checking
make gitlint          # Commit-Message Validierung
```

### Dokumentation

```bash
make gen-docs         # OpenAPI & Doku generieren (docs/_generated/)
make docs             # HTML-Dokumentation bauen (build/docs/html/)
make read-docs        # Doku im Browser öffnen
```

### Docker & Production

```bash
make docker-build     # Docker-Image bauen
make docker-run       # Docker-Image ausführen (Port 8503, 8504)
```

---

## 📦 Wichtige Python-Module

### Server-Modul (`server/`)

- FastAPI-Anwendung
- Routen für API & Dashboard
- EOSDash Web-UI (Bokeh-basiert)

### Konfiguration (`config/`)

- Pydantic-Modelle für alle Config-Parameter
- Hot-Reload Support
- Validierung & Typ-Sicherheit

### Optimierung (`optimization/`)

- Genetische Algorithmen (DEAP)
- Energiefluss-Optimierung
- Szenarien-Simulation

### Vorhersagen (`prediction/`)

- PV-Prognosen (pvlib, Akkudoktor, VRM)
- Wetter-Integration (Bright Sky, Clear Outside)
- Strompreis-Daten

### Datengeräte (`devices/`)

- Battery (Batteriespeicher)
- PV (Photovoltaik)
- Inverter (Wechselrichter)
- HeatPump (Wärmepumpe)
- ElectricVehicle (E-Fahrzeuge)

### Datenbank (`data/`)

- LMDB-basierte Persistierung
- Caching & Komprimierung
- Zeitreihen-Management

---

## 🏠 Home Assistant Integration

### Deployment

- **Add-on Store**: Über Repository installierbar
- **Baseimage**: Debian (trixie)
- **Architektur**: amd64, aarch64 (armv8)
- **Config-Datei**: [config.yaml](config.yaml)

### Docker Multi-Arch Build

```yaml
# build.yaml
amd64: ghcr.io/home-assistant/amd64-base-debian:trixie
aarch64: ghcr.io/home-assistant/aarch64-base-debian:trixie
```

---

## 📚 Dokumentation

- **README.md**: Überblick, Quick Start, Installation
- **DOCS.md**: Home Assistant Add-on Doku
- **CONTRIBUTING.md**: Beitrag zum Projekt
- **docs/**: Sphinx-Doku (conf.py, index.md)
- **openapi.json**: API-Spezifikation (auto-generated)

---

## 🔗 Community & Links

- **GitHub**: https://github.com/Akkudoktor-EOS/EOS
- **Forum**: https://www.akkudoktor.net/c/der-akkudoktor/eos
- **YouTube**: Der Akkudoktor, meintechblog
- **Docker Hub**: akkudoktor/eos:latest

---

## 📝 Lizenz & Autor

- **Lizenz**: Siehe [LICENSE](LICENSE)
- **Autor**: Andreas Schmitz (@Akkudoktor)
- **Status**: Alpha (V0.3+)

---

_Diese Übersicht wurde am 2026-03-04 erstellt und kann bei Bedarf aktualisiert werden._
