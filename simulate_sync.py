#!/usr/bin/env python3

import argparse
import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean


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


@dataclass
class PendingUpdate:
    content_id: int
    minute: int
    entity_id: int
    entity_class: str
    base_cloud_content_id: int


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


def should_accept_lww(update_minute: int, cloud_minute: int) -> bool:
    return update_minute >= cloud_minute


def resolve_conflict(policy: str, update: PendingUpdate, cloud_minute: int) -> str:
    if policy == "last_write_wins":
        return "accept" if should_accept_lww(update.minute, cloud_minute) else "reject"
    if policy == "cloud_preferred":
        return "reject"
    if policy == "display_preferred":
        return "accept"
    if policy == "manual_review_all":
        return "manual"
    if policy == "domain_aware":
        if update.entity_class == "high":
            return "manual"
        return "accept" if should_accept_lww(update.minute, cloud_minute) else "reject"
    raise ValueError(f"Unknown policy: {policy}")


def run_once(
    scenario: Scenario,
    policy: str,
    config: dict,
    replication: int,
    realization: SimulationRealization,
) -> dict[str, float | int | str]:
    day_minutes = int(config["day_minutes"])
    entity_classes = build_entities(int(config["entity_count"]), config["entity_class_mix"])
    entity_count = len(entity_classes)
    events = realization.events
    connectivity = realization.connectivity

    cloud_content = [0] * entity_count
    cloud_last_minute = [-1] * entity_count
    display_content = [[0] * entity_count for _ in range(scenario.fleet_size)]
    stale_sets: list[set[int]] = [set() for _ in range(scenario.fleet_size)]
    pending: list[list[PendingUpdate]] = [[] for _ in range(scenario.fleet_size)]

    next_content_id = 1
    event_index = 0
    sync_messages = 0
    silent_overwrites = 0
    high_risk_silent_overwrites = 0
    manual_reviews = 0
    high_risk_manual_reviews = 0
    accepted_updates = 0
    conflict_count = 0
    high_risk_conflicts = 0
    local_superseded_updates = 0
    stale_entity_display_minutes = 0

    def refresh_stale_for_entity(entity_id: int) -> None:
        for display_id in range(scenario.fleet_size):
            if display_content[display_id][entity_id] != cloud_content[entity_id]:
                stale_sets[display_id].add(entity_id)
            else:
                stale_sets[display_id].discard(entity_id)

    def set_display_content(display_id: int, entity_id: int, content_id: int) -> None:
        display_content[display_id][entity_id] = content_id
        if content_id != cloud_content[entity_id]:
            stale_sets[display_id].add(entity_id)
        else:
            stale_sets[display_id].discard(entity_id)

    for minute in range(day_minutes):
        while event_index < len(events) and events[event_index].minute == minute:
            event = events[event_index]
            content_id = next_content_id
            next_content_id += 1

            if event.source == "cloud":
                cloud_content[event.entity_id] = content_id
                cloud_last_minute[event.entity_id] = minute
                accepted_updates += 1
                refresh_stale_for_entity(event.entity_id)
            else:
                assert event.display_id is not None
                display_id = event.display_id
                set_display_content(display_id, event.entity_id, content_id)

                existing_index = next(
                    (
                        i
                        for i, update in enumerate(pending[display_id])
                        if update.entity_id == event.entity_id
                    ),
                    None,
                )
                if existing_index is None:
                    pending[display_id].append(
                        PendingUpdate(
                            content_id=content_id,
                            minute=minute,
                            entity_id=event.entity_id,
                            entity_class=event.entity_class,
                            base_cloud_content_id=cloud_content[event.entity_id],
                        )
                    )
                else:
                    previous = pending[display_id][existing_index]
                    pending[display_id][existing_index] = PendingUpdate(
                        content_id=content_id,
                        minute=minute,
                        entity_id=event.entity_id,
                        entity_class=event.entity_class,
                        base_cloud_content_id=previous.base_cloud_content_id,
                    )
                    local_superseded_updates += 1
            event_index += 1

        if minute % scenario.sync_interval_minutes == 0:
            for display_id in range(scenario.fleet_size):
                if not connectivity[display_id][minute]:
                    continue

                remaining_pending: list[PendingUpdate] = []
                pending_entities = {u.entity_id for u in pending[display_id]}

                for update in pending[display_id]:
                    sync_messages += 1
                    conflict = update.base_cloud_content_id != cloud_content[update.entity_id]
                    if conflict:
                        conflict_count += 1
                        if update.entity_class == "high":
                            high_risk_conflicts += 1
                        decision = resolve_conflict(
                            policy,
                            update,
                            cloud_last_minute[update.entity_id],
                        )
                    else:
                        decision = "accept"

                    if decision == "accept":
                        if conflict:
                            silent_overwrites += 1
                            if update.entity_class == "high":
                                high_risk_silent_overwrites += 1
                        cloud_content[update.entity_id] = update.content_id
                        cloud_last_minute[update.entity_id] = update.minute
                        refresh_stale_for_entity(update.entity_id)
                        set_display_content(display_id, update.entity_id, update.content_id)
                        accepted_updates += 1
                    elif decision == "reject":
                        if conflict:
                            silent_overwrites += 1
                            if update.entity_class == "high":
                                high_risk_silent_overwrites += 1
                        set_display_content(display_id, update.entity_id, cloud_content[update.entity_id])
                    elif decision == "manual":
                        manual_reviews += 1
                        if update.entity_class == "high":
                            high_risk_manual_reviews += 1
                        set_display_content(display_id, update.entity_id, cloud_content[update.entity_id])
                    else:
                        remaining_pending.append(update)

                pending[display_id] = remaining_pending

                for entity_id in list(stale_sets[display_id]):
                    if entity_id in pending_entities:
                        continue
                    set_display_content(display_id, entity_id, cloud_content[entity_id])
                    sync_messages += 1

        stale_entity_display_minutes += sum(len(stale) for stale in stale_sets)

    end_stale_pairs = sum(len(stale) for stale in stale_sets)

    possible_pairs = max(1, scenario.fleet_size * entity_count * day_minutes)
    stale_ratio = stale_entity_display_minutes / possible_pairs

    return {
        "replication": replication,
        "policy": policy,
        "fleet_size": scenario.fleet_size,
        "connectivity": scenario.connectivity.name,
        "online_probability": scenario.connectivity.online_probability,
        "mean_outage_minutes": scenario.connectivity.mean_outage_minutes,
        "sync_interval_minutes": scenario.sync_interval_minutes,
        "updates_per_display_day": scenario.updates_per_display_day,
        "conflict_bias": scenario.conflict_bias,
        "high_risk_update_share": scenario.high_risk_update_share,
        "events_total": len(events),
        "conflicts": conflict_count,
        "high_risk_conflicts": high_risk_conflicts,
        "silent_overwrites": silent_overwrites,
        "high_risk_silent_overwrites": high_risk_silent_overwrites,
        "manual_reviews": manual_reviews,
        "high_risk_manual_reviews": high_risk_manual_reviews,
        "accepted_updates": accepted_updates,
        "sync_messages": sync_messages,
        "local_superseded_updates": local_superseded_updates,
        "stale_entity_display_minutes": stale_entity_display_minutes,
        "stale_ratio": stale_ratio,
        "end_stale_pairs": end_stale_pairs,
    }


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


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def group_average(rows: list[dict[str, object]], keys: list[str]) -> list[dict[str, object]]:
    buckets: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row[k] for k in keys)].append(row)

    metric_names = [
        "events_total",
        "conflicts",
        "high_risk_conflicts",
        "silent_overwrites",
        "high_risk_silent_overwrites",
        "manual_reviews",
        "high_risk_manual_reviews",
        "accepted_updates",
        "sync_messages",
        "local_superseded_updates",
        "stale_entity_display_minutes",
        "stale_ratio",
        "end_stale_pairs",
    ]
    output: list[dict[str, object]] = []
    for group_values, group_rows in sorted(buckets.items()):
        item = {key: value for key, value in zip(keys, group_values)}
        item["runs"] = len(group_rows)
        for metric in metric_names:
            item[metric] = round(mean(float(r[metric]) for r in group_rows), 6)
        output.append(item)
    return output


def svg_bar_chart(
    path: Path,
    title: str,
    labels: list[str],
    values: list[float],
    y_label: str,
) -> None:
    width = 920
    height = 520
    margin_left = 90
    margin_right = 40
    margin_top = 70
    margin_bottom = 110
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1.0)
    bar_gap = 18
    bar_width = (plot_width - bar_gap * (len(labels) - 1)) / max(1, len(labels))
    colors = ["#276FBF", "#6A994E", "#F4A261", "#9B5DE5", "#D62828"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="34" text-anchor="middle" font-family="Arial" font-size="22" font-weight="700">{title}</text>',
        f'<text x="24" y="{margin_top + plot_height / 2}" transform="rotate(-90 24 {margin_top + plot_height / 2})" text-anchor="middle" font-family="Arial" font-size="14">{y_label}</text>',
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" stroke="#333" stroke-width="1"/>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#333" stroke-width="1"/>',
    ]

    for tick in range(5):
        value = max_value * tick / 4
        y = margin_top + plot_height - (value / max_value) * plot_height
        parts.append(f'<line x1="{margin_left - 5}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#e7e7e7" stroke-width="1"/>')
        parts.append(f'<text x="{margin_left - 10}" y="{y + 4:.1f}" text-anchor="end" font-family="Arial" font-size="12">{value:.1f}</text>')

    for i, (label, value) in enumerate(zip(labels, values)):
        x = margin_left + i * (bar_width + bar_gap)
        bar_height = (value / max_value) * plot_height
        y = margin_top + plot_height - bar_height
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{colors[i % len(colors)]}"/>')
        parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-family="Arial" font-size="12">{value:.2f}</text>')
        parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{margin_top + plot_height + 24}" text-anchor="middle" font-family="Arial" font-size="12">{label.replace("_", " ")}</text>')

    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def make_figures(out_dir: Path, policy_summary: list[dict[str, object]]) -> None:
    labels = [str(row["policy"]) for row in policy_summary]
    silent = [float(row["high_risk_silent_overwrites"]) for row in policy_summary]
    manual = [float(row["high_risk_manual_reviews"]) for row in policy_summary]
    stale = [float(row["stale_ratio"]) * 100.0 for row in policy_summary]

    svg_bar_chart(
        out_dir / "figures" / "high_risk_silent_overwrites.svg",
        "High-risk silent overwrites by policy",
        labels,
        silent,
        "Mean count per run",
    )
    svg_bar_chart(
        out_dir / "figures" / "high_risk_manual_reviews.svg",
        "High-risk manual reviews by policy",
        labels,
        manual,
        "Mean count per run",
    )
    svg_bar_chart(
        out_dir / "figures" / "stale_ratio.svg",
        "Mean stale entity-display ratio by policy",
        labels,
        stale,
        "Percent of entity-display-minutes",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()

    config = read_config(args.config)
    root_rng = random.Random(int(config["seed"]))
    rows: list[dict[str, object]] = []
    scenarios = scenario_grid(config)
    policies = list(config["policies"])
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
            for policy in policies:
                row = run_once(scenario, policy, config, replication, realization)
                row["scenario_index"] = scenario_index
                row["run_seed"] = run_seed
                row["realization_seed"] = run_seed
                rows.append(row)

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "raw_runs.csv", rows)

    policy_summary = group_average(rows, ["policy"])
    scenario_summary = group_average(
        rows,
        [
            "connectivity",
            "sync_interval_minutes",
            "updates_per_display_day",
            "conflict_bias",
            "high_risk_update_share",
            "policy",
        ],
    )
    high_risk_summary = group_average(
        rows,
        ["policy", "high_risk_update_share", "conflict_bias"],
    )
    write_csv(args.out / "policy_summary.csv", policy_summary)
    write_csv(args.out / "scenario_summary.csv", scenario_summary)
    write_csv(args.out / "high_risk_summary.csv", high_risk_summary)
    make_figures(args.out, policy_summary)

    print(f"Completed {len(rows)} runs")
    print(f"Wrote outputs to {args.out}/ folder")


if __name__ == "__main__":
    main()
