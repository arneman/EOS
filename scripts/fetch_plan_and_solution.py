#!/usr/bin/env python3
"""Fetch EOS plan and optimization solution snapshots.

This script stores four files with a shared timestamp suffix:
- plan_<timestamp>.json
- optimization_solution_<timestamp>.json
- optimization_load_energy_wh_<timestamp>.json
- optimization_load_energy_wh_array_<timestamp>.json

It intentionally mirrors the format used in earlier manual fetches.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import requests


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch EOS plan and optimization solution")
    parser.add_argument(
        "--base-url",
        default="http://proxmox-scripts:8503",
        help="EOS API base URL (default: http://proxmox-scripts:8503)",
    )
    parser.add_argument(
        "--output-dir",
        default="snapshots",
        help="Directory where snapshot files are written (default: snapshots)",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Optional timestamp suffix, format YYYYMMDD_HHMMSS",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds (default: 20)",
    )
    return parser.parse_args()


def _fetch_json(url: str, timeout: float) -> dict:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _write_json_pretty(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")


def _extract_load_energy_wh(solution: dict) -> list[float | str]:
    solution_data = solution.get("solution", {}).get("data", {})
    if not isinstance(solution_data, dict):
        raise ValueError("Unexpected optimization solution format: solution.data missing or invalid")

    load_values: list[float | str] = []
    for entry in solution_data.values():
        if not isinstance(entry, dict) or "load_energy_wh" not in entry:
            raise ValueError("Unexpected optimization solution format: load_energy_wh missing")
        load_values.append(float(entry["load_energy_wh"]))

    load_values.append("float64")
    return load_values


def _write_load_scalar_lines(path: Path, load_values: list[float | str]) -> None:
    lines = []
    for value in load_values:
        if isinstance(value, str):
            lines.append(json.dumps(value))
        else:
            lines.append(str(value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_load_array(path: Path, load_values: list[float | str]) -> None:
    path.write_text(json.dumps(load_values, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base_url = args.base_url.rstrip("/")
    plan_url = f"{base_url}/v1/energy-management/plan"
    optimization_url = f"{base_url}/v1/energy-management/optimization/solution"

    plan = _fetch_json(plan_url, args.timeout)
    solution = _fetch_json(optimization_url, args.timeout)

    plan_path = output_dir / f"plan_{timestamp}.json"
    solution_path = output_dir / f"optimization_solution_{timestamp}.json"
    load_scalar_path = output_dir / f"optimization_load_energy_wh_{timestamp}.json"
    load_array_path = output_dir / f"optimization_load_energy_wh_array_{timestamp}.json"

    _write_json_pretty(plan_path, plan)
    _write_json_pretty(solution_path, solution)

    load_values = _extract_load_energy_wh(solution)
    _write_load_scalar_lines(load_scalar_path, load_values)
    _write_load_array(load_array_path, load_values)

    print(plan_path)
    print(solution_path)
    print(load_scalar_path)
    print(load_array_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())