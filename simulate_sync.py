#!/usr/bin/env python3

import json
import argparse
import random
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


@dataclass
class Event:
    minute: int
    source: str
    display_id: int | None
    entity_id: int
    entity_class: str


@dataclass(frozen=True)
class SimulationRealization:
    events: list[Event]
    connectivity: list[list[bool]]


def read_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_entities(count: int, mix: dict[str, float]) -> list[str]:
    classes: list[str] = []
    remaining = count
    for entity_class in ENTITY_CLASSES[:-1]:
        n = int(round(count * mix[entity_class]))
        classes.extend([entity_class] * n)
        remaining -= n
    classes.extend(["low"] * remaining)
    return classes[:count]


def weighted_entity_class(rng: random.Random, high_share: float) -> str:
    medium_share = max(0.0, min(1.0, (1.0 - high_share) * 0.57))
    low_share = max(0.0, 1.0 - high_share - medium_share)
    draw = rng.random()
    if draw < high_share:
        return "high"
    if draw < high_share + medium_share:
        return "medium"
    return "low"


def choose_entity(
    rng: random.Random,
    entity_classes: list[str],
    desired_class: str,
    pending_by_entity: dict[int, int],
    conflict_bias: float,
) -> int:
    candidates = [i for i, c in enumerate(
        entity_classes) if c == desired_class]
    if pending_by_entity and rng.random() < conflict_bias:
        pending_candidates = [
            i for i in pending_by_entity if entity_classes[i] == desired_class]
        if pending_candidates:
            return rng.choice(pending_candidates)
    return rng.choice(candidates)


def generate_events(
    rng: random.Random,
    scenario: Scenario,
    entity_classes: list[str],
    day_minutes: int,
    cloud_update_ratio: float,
) -> list[Event]:
    total_display_events = scenario.fleet_size * scenario.updates_per_display_day
    total_cloud_events = int(round(total_display_events * cloud_update_ratio))
    events: list[Event] = []
    pending_target_hint: dict[int, int] = {}

    for _ in range(total_display_events):
        minute = rng.randrange(day_minutes)
        source_display = rng.randrange(scenario.fleet_size)
        entity_class = weighted_entity_class(
            rng, scenario.high_risk_update_share)
        entity_id = choose_entity(
            rng,
            entity_classes,
            entity_class,
            pending_target_hint,
            scenario.conflict_bias,
        )
        pending_target_hint[entity_id] = pending_target_hint.get(
            entity_id, 0) + 1
        events.append(Event(minute, "display", source_display,
                      entity_id, entity_class))

    for _ in range(total_cloud_events):
        minute = rng.randrange(day_minutes)
        entity_class = weighted_entity_class(
            rng, scenario.high_risk_update_share)
        entity_id = choose_entity(
            rng,
            entity_classes,
            entity_class,
            pending_target_hint,
            scenario.conflict_bias,
        )
        pending_target_hint[entity_id] = pending_target_hint.get(
            entity_id, 0) + 1
        events.append(Event(minute, "cloud", None, entity_id, entity_class))

    events.sort(key=lambda e: (e.minute, e.source))
    return events


def generate_connectivity(
    rng: random.Random,
    scenario: Scenario,
    day_minutes: int,
) -> list[list[bool]]:
    p_online = scenario.connectivity.online_probability
    mean_outage = max(1.0, scenario.connectivity.mean_outage_minutes)
    mean_online = mean_outage * p_online / max(0.01, 1.0 - p_online)
    drop_probability = min(1.0, 1.0 / max(1.0, mean_online))
    recover_probability = min(1.0, 1.0 / mean_outage)
    all_states: list[list[bool]] = []

    for _ in range(scenario.fleet_size):
        online = rng.random() < p_online
        states: list[bool] = []
        for _minute in range(day_minutes):
            states.append(online)
            if online and rng.random() < drop_probability:
                online = False
            elif not online and rng.random() < recover_probability:
                online = True
        all_states.append(states)
    return all_states


def generate_realization(
    rng: random.Random,
    scenario: Scenario,
    entity_classes: list[str],
    config: dict,
) -> SimulationRealization:
    day_minutes = int(config["day_minutes"])
    return SimulationRealization(
        events=generate_events(
            rng,
            scenario,
            entity_classes,
            day_minutes,
            float(config["cloud_update_ratio"]),
        ),
        connectivity=generate_connectivity(rng, scenario, day_minutes),
    )


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
    root_rng = random.Random(int(config["seed"]))
    scenarios = scenario_grid(config)
    replications = int(config["replications"])

    for scenario_index, scenario in enumerate(scenarios):
        entity_classes = build_entities(int(config["entity_count"]), config["entity_class_mix"])
        for replication in range(replications):
            run_seed = root_rng.randrange(1_000_000_000)
            realization = generate_realization(
                random.Random(run_seed),
                scenario,
                entity_classes,
                config,
            )


if __name__ == "__main__":
    main()
