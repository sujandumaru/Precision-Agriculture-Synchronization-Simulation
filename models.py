"""Data structures for the synchronization sensitivity simulation."""

from dataclasses import dataclass


ENTITY_CLASSES = ("high", "medium", "low")


@dataclass(frozen=True)
class ConnectivityScenario:
    """Connectivity assumptions for one simulated rural network condition."""

    name: str
    online_probability: float
    mean_outage_minutes: float


@dataclass(frozen=True)
class Scenario:
    """One parameter combination in the synchronization sensitivity sweep."""

    fleet_size: int
    connectivity: ConnectivityScenario
    sync_interval_minutes: int
    updates_per_display_day: int
    conflict_bias: float
    high_risk_update_share: float


@dataclass
class Event:
    """A generated cloud-side or display-side setup-data update event."""

    minute: int
    source: str
    display_id: int | None
    entity_id: int
    entity_class: str


@dataclass
class PendingUpdate:
    """A display update waiting for synchronization with the cloud state."""

    content_id: int
    minute: int
    entity_id: int
    entity_class: str
    base_cloud_content_id: int


@dataclass(frozen=True)
class SimulationRealization:
    """Generated event stream and connectivity states shared across policies."""

    events: list[Event]
    connectivity: list[list[bool]]


def build_entities(count: int, mix: dict[str, float]) -> list[str]:
    """Create entity risk-class labels according to the configured mix."""

    classes: list[str] = []
    remaining = count
    for entity_class in ENTITY_CLASSES[:-1]:
        n = int(round(count * mix[entity_class]))
        classes.extend([entity_class] * n)
        remaining -= n
    classes.extend(["low"] * remaining)
    return classes[:count]


def scenario_grid(config: dict) -> list[Scenario]:
    """Expand the configuration sweep into concrete simulation scenarios."""

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
