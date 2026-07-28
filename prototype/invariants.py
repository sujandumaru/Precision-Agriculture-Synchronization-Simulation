"""Safety invariants checked against the audit log and final system state."""
from __future__ import annotations


def check(audit: list[dict], cloud_state: dict, display_hashes: list[dict],
          policy: str, review_queue: list[dict]) -> list[dict]:
    results = []

    def add(name, ok, detail=""):
        results.append({"invariant": name, "passed": bool(ok), "detail": detail})

    # I1 - under domain_aware, no high-integrity concurrent update is ever
    #      resolved without a human in the loop
    bad = [r for r in audit if r["kind"] == "conflict_resolved"
           and r.get("cls") == "high" and r.get("policy") == "domain_aware"
           and r.get("action") != "queue"]
    add("I1_high_integrity_never_auto_resolved",
        not bad if policy == "domain_aware" else True,
        f"{len(bad)} violation(s)" if policy == "domain_aware" else "n/a for this policy")

    # I2 - every conflict retains BOTH branches in the durable audit record
    missing = [r for r in audit if r["kind"] == "conflict_resolved"
               and not (r.get("branches", {}).get("display") and r.get("branches", {}).get("cloud"))]
    add("I2_both_branches_retained", not missing, f"{len(missing)} record(s) missing a branch")

    # I3 - replicas converge once connectivity returns
    diverged = []
    cloud_hashes = {k: v["hash"] for k, v in cloud_state.items()}
    for i, dh in enumerate(display_hashes):
        for eid, h in dh.items():
            if eid in cloud_hashes and cloud_hashes[eid] != h:
                diverged.append((i, eid))
    add("I3_convergence_after_reconnect", not diverged,
        f"{len(diverged)} entity/display pair(s) divergent")

    # I4 - duplicate delivery is idempotent (suppressed, never re-applied)
    dups = [r for r in audit if r["kind"] == "duplicate_suppressed"]
    applied_ids = [r["update_id"] for r in audit
                   if r["kind"] in ("accept_fastforward", "conflict_resolved")]
    add("I4_duplicate_delivery_idempotent", len(applied_ids) == len(set(applied_ids)),
        f"{len(dups)} duplicate(s) suppressed, {len(applied_ids) - len(set(applied_ids))} re-applied")

    # I5 - every queued conflict is actually present in the review queue
    queued = [r for r in audit if r["kind"] == "conflict_resolved" and r.get("action") == "queue"]
    add("I5_queued_conflicts_persisted", len(queued) == len(review_queue),
        f"audit={len(queued)} queue={len(review_queue)}")

    return results


def metrics_from_audit(audit: list[dict]) -> dict:
    """Reconstruct all outcome metrics from the durable audit log alone.

    In-process counters are lost when the cloud is killed, so the audit log is
    the only trustworthy source across a crash. That the two agree on a clean
    run is itself a check on the log's completeness.
    """
    m = {"conflicts": 0, "high_risk_conflicts": 0, "silent_overwrites": 0,
         "high_risk_silent_overwrites": 0, "manual_reviews": 0,
         "high_risk_manual_reviews": 0, "merges": 0, "contested_field_merges": 0,
         "accepted": 0, "duplicates_suppressed": 0, "stale_rejected": 0}
    for r in audit:
        k = r["kind"]
        if k == "accept_fastforward":
            m["accepted"] += 1
        elif k == "duplicate_suppressed":
            m["duplicates_suppressed"] += 1
        elif k == "stale_update":
            m["stale_rejected"] += 1
        elif k == "conflict_resolved":
            high = r.get("cls") == "high"
            m["conflicts"] += 1
            m["high_risk_conflicts"] += high
            act = r.get("action")
            if act in ("accept", "reject"):
                m["silent_overwrites"] += 1
                m["high_risk_silent_overwrites"] += high
            elif act == "queue":
                m["manual_reviews"] += 1
                m["high_risk_manual_reviews"] += high
            elif act == "merge":
                m["merges"] += 1
                if r.get("discarded_branch"):
                    m["contested_field_merges"] += 1
                    m["silent_overwrites"] += 1
                    m["high_risk_silent_overwrites"] += high
    return m
