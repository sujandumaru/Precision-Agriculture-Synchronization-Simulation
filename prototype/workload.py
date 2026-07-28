"""Deterministic workload generation, aligned with the simulation study's factors."""
from __future__ import annotations

import random

from common import ENTITY_CLASSES, FIELDS


def build_entities(count: int, mix: dict[str, float]) -> list[dict]:
    classes: list[str] = []
    remaining = count
    for cls in ENTITY_CLASSES[:-1]:
        n = int(round(count * mix[cls]))
        classes.extend([cls] * n)
        remaining -= n
    classes.extend(["low"] * remaining)
    classes = classes[:count]
    return [{"entity_id": f"E{i:04d}", "cls": c,
             "fields": {f: {"v": f"{f}-init", "ts": 0, "node": "seed"} for f in FIELDS},
             "vv": {}} for i, c in enumerate(classes)]


def weighted_class(rng: random.Random, high_share: float) -> str:
    medium = max(0.0, min(1.0, (1.0 - high_share) * 0.57))
    d = rng.random()
    if d < high_share:
        return "high"
    if d < high_share + medium:
        return "medium"
    return "low"


def connectivity_trace(rng: random.Random, ticks: int, p_online: float,
                       mean_outage: float) -> list[bool]:
    """Two-state online/offline process, same construction as simulate_sync.py."""
    mean_outage = max(1.0, mean_outage)
    mean_online = mean_outage * p_online / max(0.01, 1.0 - p_online)
    p_drop = min(1.0, 1.0 / max(1.0, mean_online))
    p_recover = min(1.0, 1.0 / mean_outage)
    online = rng.random() < p_online
    out = []
    for _ in range(ticks):
        out.append(online)
        if online and rng.random() < p_drop:
            online = False
        elif not online and rng.random() < p_recover:
            online = True
    return out


def generate_plan(rng: random.Random, cfg: dict, entities: list[dict]) -> dict:
    """Produce the shared workload replayed identically under every policy."""
    ticks = cfg["ticks"]
    fleet = cfg["fleet_size"]
    by_class: dict[str, list[str]] = {c: [] for c in ENTITY_CLASSES}
    for e in entities:
        by_class[e["cls"]].append(e["entity_id"])

    display_events = []
    hot: dict[str, int] = {}
    n_display = fleet * cfg["updates_per_display_day"]
    for _ in range(n_display):
        tick = rng.randrange(ticks)
        node = rng.randrange(fleet)
        cls = weighted_class(rng, cfg["high_risk_update_share"])
        pool = by_class[cls]
        if hot and rng.random() < cfg["conflict_bias"]:
            hp = [e for e in hot if e in pool]
            eid = rng.choice(hp) if hp else rng.choice(pool)
        else:
            eid = rng.choice(pool)
        hot[eid] = hot.get(eid, 0) + 1
        display_events.append({"tick": tick, "node": node, "entity_id": eid, "cls": cls,
                               "field": rng.choice(FIELDS), "value": f"d{node}-t{tick}"})

    cloud_events = []
    for _ in range(int(round(n_display * cfg["cloud_update_ratio"]))):
        tick = rng.randrange(ticks)
        cls = weighted_class(rng, cfg["high_risk_update_share"])
        pool = by_class[cls]
        if hot and rng.random() < cfg["conflict_bias"]:
            hp = [e for e in hot if e in pool]
            eid = rng.choice(hp) if hp else rng.choice(pool)
        else:
            eid = rng.choice(pool)
        hot[eid] = hot.get(eid, 0) + 1
        cloud_events.append({"tick": tick, "entity_id": eid, "cls": cls,
                             "field": rng.choice(FIELDS), "value": f"cloud-t{tick}"})

    display_events.sort(key=lambda e: (e["tick"], e["node"]))
    cloud_events.sort(key=lambda e: e["tick"])
    conn = [connectivity_trace(rng, ticks, cfg["online_probability"],
                               cfg["mean_outage_ticks"]) for _ in range(fleet)]
    return {"display_events": display_events, "cloud_events": cloud_events,
            "connectivity": conn}
