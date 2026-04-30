#!/usr/bin/env python3

import json
import argparse
from dataclasses import dataclass
from pathlib import Path


ENTITY_CLASSES = ("high", "medium", "low")


@dataclass(frozen=True)
class ConnectivityScenario:
    name: str
    online_probability: float
    mean_outage_minutes: float


@dataclass(frozen=True)
class Scenario:
    fleet_size: int
    connectivity: ConnectivityScenario
    sync_interval_minutes: int
    updates_per_display_day: int
    conflict_bias: float
    high_risk_update_share: float


def build_entities(count: int, mix: dict[str, float]) -> list[str]:
    classes: list[str] = []
    remaining = count
    for entity_class in ENTITY_CLASSES[:-1]:
        n = int(round(count * mix[entity_class]))
        classes.extend([entity_class] * n)
        remaining -= n
    classes.extend(["low"] * remaining)
    return classes[:count]


def read_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def scenario_grid(config: dict) -> list[Scenario]:
    scenarios = config["scenarios"]
    connectivity = [
        ConnectivityScenario(
            name=c["name"],
            online_probability=float(c["online_probability"]),
            mean_outage_minutes=float(c["mean_outage_minutes"]),
        )
        for c in scenarios["connectivity"]
    ]

    grid: list[Scenario] = []
    for fleet_size in scenarios["fleet_size"]:
        for conn in connectivity:
            for sync_interval in scenarios["sync_interval_minutes"]:
                for updates in scenarios["updates_per_display_day"]:
                    for conflict_bias in scenarios["conflict_bias"]:
                        for high_share in scenarios["high_risk_update_share"]:
                            grid.append(
                                Scenario(
                                    fleet_size=int(fleet_size),
                                    connectivity=conn,
                                    sync_interval_minutes=int(sync_interval),
                                    updates_per_display_day=int(updates),
                                    conflict_bias=float(conflict_bias),
                                    high_risk_update_share=float(high_share),
                                )
                            )
    return grid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()

    config = read_config(args.config)

    entity_classes = build_entities(int(config["entity_count"]), config["entity_class_mix"])
    scenarios = scenario_grid(config)


if __name__ == "__main__":
    main()
