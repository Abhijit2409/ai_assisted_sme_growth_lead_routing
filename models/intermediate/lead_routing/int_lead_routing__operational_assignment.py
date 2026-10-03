"""Capacity-aware operational RM assignment: a sequential simulation with an RM capacity ledger.

Grain: one row per lead per routing-decision timestamp. Primary key: decision_id.

Why this model exists
---------------------
The static recommendation (mart_routing_recommendation_static) ranks candidates independently at every decision and
never consumes capacity, so the same lightly loaded RM can win thousands of times. That makes it a useful
*diagnostic* of rule fit, but it is not an operational assignment: it could not be executed as it stands.

This model answers the operational question instead. If the declared rules were applied decision by decision, in
order, and each assignment actually consumed the chosen RM's capacity, who would receive each lead and which leads
would have to be held?

Declared processing order (routing_capacity_ledger_config.processing_order)
--------------------------------------------------------------------------
1. routing decision timestamp, ascending - capacity is consumed in time order;
2. priority tier, P1 before P2 before P3 - at an identical instant the more urgent lead claims capacity first;
3. priority score, descending;
4. maximum SLA pressure at the decision, descending;
5. decision_id, ascending - a stable final tie-break, so the run is reproducible.

Ledger logic (routing_capacity_ledger_config)
---------------------------------------------
The ledger holds, per RM, the capacity snapshot it is currently based on and the simulated workload added since
that snapshot.

* For each candidate the model uses the latest valid capacity snapshot at or before the decision instant, already
  resolved as-of in int_lead_routing__candidate_eligibility. Nothing later than the decision is read.
* If that snapshot is newer than the RM's current ledger basis, the observed snapshot replaces the basis and the
  simulated additions reset to zero. The observed snapshot already reflects the work the RM is really carrying, so
  continuing to add to the older basis would double-count.
* simulated post-assignment workload = observed weighted workload at the basis snapshot
  + simulated additions since that basis + the workload one new assignment adds
  (assignment_workload_points = 1.00, the weight CAP-01 gives an assigned, not-yet-contacted lead).
* A candidate is selectable only while that simulated post-assignment workload stays STRICTLY BELOW the declared
  limit (post_assignment_limit_ratio of the RM's capacity, CAP-06) - the same strict test REL-13 applies before the
  assignment. An ordinary assignment must never push an RM to or past the hard limit, so a candidate whose workload
  would land exactly on it is skipped.
* Candidates are tried in the declared preference order (candidate_rank). The first with capacity is chosen and its
  ledger balance is incremented by the assignment workload.
* If no eligible candidate has capacity, the lead is held in the declared capacity queue rather than assigned beyond
  capacity.

Point-in-time safety
--------------------
The model reads only the as-of candidate set, the as-of capacity snapshot and the as-of priority. It never reads a
conversion outcome, an opportunity record, a ground-truth label, the historical selected RM or any event after the
decision instant. The ledger is built strictly forward in time from this model's own earlier decisions.

Hypothetical rules for a fictional bank over entirely synthetic data. The result is a simulated operational
recommendation, not a record of anything that happened.
"""

COLUMNS = [
    ("decision_id", "varchar"), ("lead_id", "varchar"), ("as_of_timestamp", "timestamp"), ("as_of_date", "date"),
    ("decision_trigger", "varchar"), ("decision_type", "varchar"), ("is_capacity_warm_up_period", "boolean"),
    ("territory_id", "varchar"), ("specialist_requirement_level", "varchar"), ("stated_service_language", "varchar"),
    ("priority_tier", "varchar"), ("priority_score", "double"), ("max_sla_pressure_at_decision", "double"),
    ("processing_sequence", "bigint"), ("recommended_rm_id", "varchar"), ("recommendation_status", "varchar"),
    ("queue_code", "varchar"), ("queue_reason", "varchar"), ("candidate_rank", "bigint"),
    ("preference_score", "double"), ("eligible_candidate_count", "bigint"),
    ("candidates_rejected_for_capacity", "bigint"), ("capacity_snapshot_id", "varchar"),
    ("capacity_snapshot_as_of_utc", "timestamp"), ("observed_workload_points_at_snapshot", "double"),
    ("simulated_workload_added_since_snapshot", "double"),
    ("workload_points_added_by_this_recommendation", "double"),
    ("simulated_post_assignment_workload_points", "double"), ("capacity_limit_points", "double"),
    ("simulated_post_assignment_utilization", "double"), ("remaining_capacity_points_after", "double"),
    ("territory_eligibility_result", "varchar"), ("specialist_eligibility_result", "varchar"),
    ("language_eligibility_result", "varchar"), ("separating_tiebreak_id", "varchar"),
    ("tiebreak_priority_tier_order", "bigint"), ("tiebreak_score_order", "double"),
    ("tiebreak_sla_order", "double"), ("assignment_workload_points_declared", "double"),
    ("post_assignment_limit_ratio_declared", "double"), ("recommendation_basis", "varchar"),
    ("rule_version", "varchar"),
]


def _records(rel):
    """Rows of a DuckDB relation as dicts, using no third-party dataframe library."""
    cols = rel.columns
    return [dict(zip(cols, row)) for row in rel.fetchall()]


def model(dbt, session):
    dbt.config(materialized="table", tags=["lead_routing"])

    population = _records(dbt.ref("int_lead_routing__routing_decision_population"))
    priority = _records(dbt.ref("int_lead_routing__lead_priority_score"))
    features = _records(dbt.ref("int_lead_routing__priority_features_as_of"))
    candidates = _records(dbt.ref("int_lead_routing__candidate_ranking"))
    queues = _records(dbt.ref("int_lead_routing__queue_outcome"))
    cfg_rows = _records(dbt.ref("routing_capacity_ledger_config"))

    cfg = {r["parameter"]: r["value"] for r in cfg_rows}
    assignment_points = float(cfg["assignment_workload_points"])
    limit_ratio = float(cfg["post_assignment_limit_ratio"])
    capacity_queue = cfg["unassigned_queue_code"]
    rule_version = cfg_rows[0]["rule_version"]

    pri = {r["decision_id"]: r for r in priority}
    feat = {r["decision_id"]: r for r in features}
    queue_by_decision = {r["decision_id"]: r for r in queues}

    # candidates in the declared preference order, grouped by decision
    by_decision = {}
    for r in sorted(candidates, key=lambda r: (r["decision_id"], r["candidate_rank"])):
        by_decision.setdefault(r["decision_id"], []).append(r)

    # ------------------------------------------------------------------ declared processing order
    tier_rank = {"P1": 1, "P2": 2, "P3": 3}

    def order_key(rec):
        p = pri.get(rec["decision_id"], {})
        f = feat.get(rec["decision_id"], {})
        tier = tier_rank.get(p.get("recommended_priority_band"), 9)
        score = p.get("recommended_priority_score")
        sla = f.get("max_sla_pressure_at_decision")
        return (rec["as_of_timestamp"], tier,
                -(float(score) if score is not None else -1.0),
                -(float(sla) if sla is not None else -1.0),
                rec["decision_id"])

    ordered = sorted(population, key=order_key)

    # ------------------------------------------------------------------ sequential pass with the capacity ledger
    ledger_basis = {}   # rm_id -> capacity_snapshot_as_of_utc the ledger is currently based on
    ledger_added = {}   # rm_id -> simulated workload points added since that basis
    rows = []

    for seq, rec in enumerate(ordered, start=1):
        did = rec["decision_id"]
        p = pri.get(did, {})
        f = feat.get(did, {})
        qq = queue_by_decision.get(did, {})
        tier = p.get("recommended_priority_band")
        score = p.get("recommended_priority_score")
        sla = f.get("max_sla_pressure_at_decision")
        cands = by_decision.get(did, [])

        row = {name: None for name, _ in COLUMNS}
        row.update({
            "decision_id": did, "lead_id": rec["lead_id"], "as_of_timestamp": rec["as_of_timestamp"],
            "as_of_date": rec["as_of_date"], "decision_trigger": rec["decision_trigger"],
            "decision_type": rec["decision_type"],
            "is_capacity_warm_up_period": rec["is_capacity_warm_up_period"],
            "territory_id": rec["territory_id"],
            "specialist_requirement_level": rec["specialist_requirement_level"],
            "stated_service_language": rec["stated_service_language"],
            "priority_tier": tier,
            "priority_score": float(score) if score is not None else None,
            "max_sla_pressure_at_decision": float(sla) if sla is not None else None,
            "processing_sequence": seq,
            "eligible_candidate_count": len(cands),
            "candidates_rejected_for_capacity": 0,
            "tiebreak_priority_tier_order": tier_rank.get(tier, 9),
            "tiebreak_score_order": -(float(score) if score is not None else -1.0),
            "tiebreak_sla_order": -(float(sla) if sla is not None else -1.0),
            "assignment_workload_points_declared": assignment_points,
            "post_assignment_limit_ratio_declared": limit_ratio,
            "recommendation_basis": "capacity_aware_operational",
            "rule_version": rule_version,
        })

        # lead-level gate: no candidate is considered at all
        if not rec["proceeds_to_candidate_evaluation"]:
            row["recommendation_status"] = "queued"
            row["queue_code"] = qq.get("queue_code") or rec["lead_gate_queue_code"]
            row["queue_reason"] = qq.get("queue_reason") or "The lead did not pass the lead-level routing gates."
            rows.append(row)
            continue

        chosen, rejected = None, 0
        for cand in cands:
            rm = cand["rm_id"]
            snap = cand["capacity_snapshot_as_of_utc"]
            # a newer observed snapshot replaces the ledger basis and clears the simulated additions
            if rm not in ledger_basis or (snap is not None and ledger_basis[rm] is not None and snap > ledger_basis[rm]):
                ledger_basis[rm] = snap
                ledger_added[rm] = 0.0
            observed = float(cand["weighted_workload_points"])
            added = float(ledger_added.get(rm, 0.0))
            limit = float(cand["capacity_limit_points"])
            post = observed + added + assignment_points
            if limit > 0 and post < limit * limit_ratio:
                chosen = (cand, observed, added, limit, post)
                break
            rejected += 1

        row["candidates_rejected_for_capacity"] = rejected

        if chosen is None:
            row["recommendation_status"] = "queued"
            if cands:
                row["queue_code"] = capacity_queue
                row["queue_reason"] = (
                    f"All {len(cands)} eligible candidates would exceed the declared capacity limit once this "
                    f"assignment is added to their simulated workload.")
            else:
                row["queue_code"] = qq.get("queue_code")
                row["queue_reason"] = qq.get("queue_reason")
            rows.append(row)
            continue

        cand, observed, added, limit, post = chosen
        rm = cand["rm_id"]
        ledger_added[rm] = added + assignment_points
        role = cand.get("territory_coverage_role")
        lvl = rec["specialist_requirement_level"]
        lang = rec["stated_service_language"]
        row.update({
            "recommended_rm_id": rm,
            "recommendation_status": "assigned",
            "candidate_rank": int(cand["candidate_rank"]),
            "preference_score": float(cand["preference_score"]),
            "capacity_snapshot_id": cand["capacity_snapshot_id"],
            "capacity_snapshot_as_of_utc": cand["capacity_snapshot_as_of_utc"],
            "observed_workload_points_at_snapshot": observed,
            "simulated_workload_added_since_snapshot": added,
            "workload_points_added_by_this_recommendation": assignment_points,
            "simulated_post_assignment_workload_points": post,
            "capacity_limit_points": limit,
            "simulated_post_assignment_utilization": (post / limit) if limit > 0 else None,
            "remaining_capacity_points_after": limit - post,
            "territory_eligibility_result": (
                "covers lead territory" if role in ("primary", "backup")
                else "national specialist pool fallback" if role == "national_pool" else "no effective coverage"),
            "specialist_eligibility_result": (
                "mandatory specialist met" if lvl == "mandatory"
                else ("preferred specialist held" if cand.get("holds_any_specialization")
                      else "generalist on a preferred-specialist industry") if lvl == "preferred"
                else "no specialist requirement"),
            "language_eligibility_result": (
                "stated language supported" if lang in ("en", "fr") else "no language stated"),
            "separating_tiebreak_id": cand.get("separating_tiebreak_id"),
        })
        rows.append(row)

    # ------------------------------------------------------------------ hand the result back as a relation
    # The rows are written once as CSV and read back with an explicit column schema. Row-by-row parameter binding
    # (executemany) against the file-backed warehouse is pathologically slow at this row count; DuckDB's CSV reader
    # is not. The file is written under dbt's target directory and is not a project deliverable.
    import csv
    import os
    import tempfile

    names = [c for c, _ in COLUMNS]
    target = os.path.join(os.getcwd(), "target")
    os.makedirs(target, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="lr_operational_", dir=target)
    os.close(fd)
    try:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator=chr(10))
            w.writerow(names)
            for r in rows:
                w.writerow(["" if r[c] is None else r[c] for c in names])
        # built by concatenation: a literal double brace would be read as Jinja when dbt renders this model
        schema = "{" + ", ".join("'" + c + "': '" + t + "'" for c, t in COLUMNS) + "}"
        csv_path = path.replace("\\", "/").replace("'", "''")
        session.execute(
            "create or replace temp table tmp_lr_operational_assignment as select * from read_csv('"
            + csv_path + "', header = true, columns = " + schema
            + ", nullstr = '', quote = '\"', escape = '\"')")
    finally:
        if os.path.exists(path):
            os.remove(path)
    return session.table("tmp_lr_operational_assignment")
