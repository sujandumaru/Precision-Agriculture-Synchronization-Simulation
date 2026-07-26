"""Conflict-resolution policies.

Policies 1-5 reproduce the five compared in the simulation study.
Policies 6-7 are causal baselines added so the comparison is not against
timestamp/authority strawmen only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import hashlib
import json

from common import vv_merge


def hash_key(key) -> int:
    return int(hashlib.sha256("|".join(key).encode()).hexdigest()[:8], 16)

POLICIES = (
    "last_write_wins",
    "cloud_preferred",
    "display_preferred",
    "manual_review_all",
    "domain_aware",
    "version_vector_causal",
    "version_vector_cloud_wins",
    "version_vector_display_wins",
    "version_vector_random",
    "crdt_field_merge",
)

# Causal detection must be followed by some tiebreak. Three additional variants
# exist so the reported result can be shown to be invariant to that choice
# rather than an artifact of one arbitrary rule.
VV_VARIANTS = ("version_vector_causal", "version_vector_cloud_wins",
               "version_vector_display_wins", "version_vector_random")


@dataclass
class Decision:
    action: str                      # accept | reject | queue | merge
    merged_fields: dict | None = None
    discarded_branch: str | None = None   # 'display' | 'cloud' | None
    note: str = ""
    diagnostics: dict = field(default_factory=dict)


def _lww_winner(display_ts: int, cloud_ts: int, display_node: str, cloud_node: str) -> str:
    """Wall-clock comparison with deterministic node-id tiebreak.

    This is the clock-skew-sensitive rule; version_vector_causal exists to
    show what changes when causality replaces wall-clock ordering.
    """
    if display_ts != cloud_ts:
        return "display" if display_ts > cloud_ts else "cloud"
    return "display" if display_node > cloud_node else "cloud"


def _merge_fields(display_fields: dict, cloud_fields: dict) -> tuple[dict, list[str]]:
    """Per-field LWW-register merge.

    A field is only *contested* when both replicas actually wrote it (neither
    stamp is the seed value) and the values differ. Concurrent edits to
    different fields both survive, which is the whole point of the baseline:
    it removes most of the data loss that whole-entity policies cause, so the
    domain-aware policy has to earn its result against a real opponent.
    """
    merged: dict[str, Any] = {}
    contested: list[str] = []
    for name in set(display_fields) | set(cloud_fields):
        d = display_fields.get(name)
        c = cloud_fields.get(name)
        if d is None or c is None:
            merged[name] = d if c is None else c
            continue
        if d["v"] == c["v"]:
            merged[name] = d if d["ts"] >= c["ts"] else c
            continue
        d_written = d.get("node") not in (None, "", "seed")
        c_written = c.get("node") not in (None, "", "seed")
        winner = _lww_winner(d["ts"], c["ts"], d.get("node", ""), c.get("node", ""))
        merged[name] = d if winner == "display" else c
        if d_written and c_written:
            # both sides genuinely wrote this field; one write is discarded
            contested.append(name)
    return merged, contested


def resolve(policy: str, ctx: dict) -> Decision:
    """Resolve one detected concurrent update under the requested policy.

    ctx keys: entity_class, display_ts, cloud_ts, display_node, cloud_node,
              display_fields, cloud_fields, display_vv, cloud_vv
    """
    cls = ctx["entity_class"]

    if policy == "cloud_preferred":
        return Decision("reject", discarded_branch="display", note="cloud authority")

    if policy == "display_preferred":
        return Decision("accept", discarded_branch="cloud", note="display authority")

    if policy == "manual_review_all":
        return Decision("queue", note="all conflicts reviewed")

    if policy == "last_write_wins":
        w = _lww_winner(ctx["display_ts"], ctx["cloud_ts"], ctx["display_node"], ctx["cloud_node"])
        return Decision(
            "accept" if w == "display" else "reject",
            discarded_branch="cloud" if w == "display" else "display",
            note="wall-clock lww",
        )

    if policy == "domain_aware":
        if cls == "high":
            return Decision("queue", note="high-integrity routed to review")
        w = _lww_winner(ctx["display_ts"], ctx["cloud_ts"], ctx["display_node"], ctx["cloud_node"])
        return Decision(
            "accept" if w == "display" else "reject",
            discarded_branch="cloud" if w == "display" else "display",
            note="lower-integrity resolved automatically",
        )

    if policy in VV_VARIANTS:
        if policy == "version_vector_cloud_wins":
            w = "cloud"
        elif policy == "version_vector_display_wins":
            w = "display"
        elif policy == "version_vector_random":
            # deterministic per-conflict pseudo-random choice, seeded by the
            # entity and the two version vectors, so runs stay reproducible
            key = (ctx["entity_id"], json.dumps(ctx["display_vv"], sort_keys=True),
                   json.dumps(ctx["cloud_vv"], sort_keys=True))
            w = "display" if (hash_key(key) & 1) else "cloud"
        else:
            w = "display" if ctx["display_node"] > ctx["cloud_node"] else "cloud"
        return Decision(
            "accept" if w == "display" else "reject",
            discarded_branch="cloud" if w == "display" else "display",
            note=f"causal detection, {policy} tiebreak",
            diagnostics={"clock_independent": True},
        )

    if policy == "__never__":
        # Causality is already known to be concurrent here, so a deterministic
        # convergent tiebreak is applied. No wall clock is consulted, which
        # removes clock-skew sensitivity, but one branch is still discarded.
        w = "display" if ctx["display_node"] > ctx["cloud_node"] else "cloud"
        return Decision(
            "accept" if w == "display" else "reject",
            discarded_branch="cloud" if w == "display" else "display",
            note="causal detection, deterministic tiebreak",
            diagnostics={"clock_independent": True},
        )

    if policy == "crdt_field_merge":
        merged, contested = _merge_fields(ctx["display_fields"], ctx["cloud_fields"])
        return Decision(
            "merge",
            merged_fields=merged,
            discarded_branch="display" if contested else None,
            note=f"field merge, {len(contested)} contested field(s)",
            diagnostics={"contested_fields": contested, "clock_independent": not contested},
        )

    raise ValueError(f"Unknown policy: {policy}")
