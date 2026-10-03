"""
Lead prioritization and capacity-aware RM routing: analysis, sensitivity, policy comparison and figures.

Reads the dbt marts that implement the framework (analytics.mart_lead_priority, mart_routing_candidate,
mart_routing_recommendation_static, mart_routing_exception, mart_routing_evaluation) and the declared
configuration seeds, all from the DuckDB warehouse (read-only). It does not re-implement the scoring or
routing logic, which lives in dbt. It adds:
- business-facing result files (priority results, component summary, band profile, recommendations, candidate
  summary, exceptions, policy comparison, specialist-demand gap, capacity profile, worked examples, findings);
- a deterministic weight-perturbation sensitivity analysis over the published normalized values;
- a pile-up diagnostic that shows what the static counterfactual cannot show;
- diagnostic figures and a figure register.

Framing, which every output repeats: these are hypothetical rules for a fictional bank, evaluated on entirely
synthetic CRM data. Differences against the synthetic historical assignments are rule-adherence comparisons,
routing-policy counterfactuals, operational-exposure measures and assignment-quality proxies. None of them is
evidence of a causal improvement in conversion, revenue, profitability or customer experience. No fictional
contact field is ever written out.

    C:/Users/abhij/anaconda3/python.exe src/analysis/18_analyze_lead_prioritization_and_routing.py [--output-root DIR]
"""

from pathlib import Path
import hashlib
import io
import sys

import duckdb
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
BASE_OUT = ROOT
if "--output-root" in sys.argv:
    BASE_OUT = Path(sys.argv[sys.argv.index("--output-root") + 1]).resolve()
OUT = BASE_OUT / "data" / "analysis" / "lead_routing"
FIG = BASE_OUT / "outputs" / "lead_routing"

PERIOD = "synthetic operating year 2025-06-01 to 2026-05-31 UTC"
PERIOD_SHORT = "synthetic year 2025-06 to 2026-05"   # figure titles; the full period is in the footnote
UNIT = "routing decision (one lead at one routing-decision timestamp)"
POPULATION = "every synthetic routing-decision timestamp, plus one intake-routability review per lead the routing engine never saw"
SOURCE = ("dbt marts: mart_lead_priority, mart_routing_candidate, mart_routing_recommendation_static, "
          "mart_routing_exception, mart_routing_evaluation")
LIMIT = ("Hypothetical rules for a fictional bank over entirely synthetic CRM data. Rule-adherence, counterfactual "
         "and operational-exposure comparisons only; never evidence of a causal effect on conversion, revenue, "
         "profitability or customer experience. Recommendations never consume capacity, so every decision is "
         "evaluated against the capacity the historical policy produced.")
NOTE = ("Hypothetical framework, fictional bank, entirely synthetic data covering 2025-06-01 to 2026-05-31 UTC. "
        "Rule adherence and operational exposure only: no causal claim about conversion, revenue, "
        "profitability or customer experience.")
BAND_COLORS = {"P1": "#b3401f", "P2": "#1f5f8b", "P3": "#7f9aa8", "(not scored)": "#b0b0b0"}
POLICY_COLORS = {"recommended": "#1f5f8b", "historical": "#e08a1e"}
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def stamp(df):
    """Every published file carries its source, period, statistical unit and evidence eligibility."""
    out = df.copy()
    out["source_models"] = SOURCE
    out["period"] = PERIOD
    out["statistical_unit"] = UNIT
    out["evidence_eligibility"] = POPULATION
    out["limitations"] = LIMIT
    return out


def load():
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    d = {
        "priority": con.execute("select * from analytics.mart_lead_priority order by decision_id").df(),
        "components": con.execute("""select * from intermediate.int_lead_routing__priority_components
                                     order by decision_id, component_id""").df(),
        "candidates": con.execute("select * from analytics.mart_routing_candidate order by decision_id, rm_id").df(),
        "recommendation": con.execute("""select * from analytics.mart_routing_recommendation_static
                                         order by decision_id""").df(),
        "operational": con.execute("""select * from analytics.mart_routing_recommendation_operational
                                      order by decision_id""").df(),
        "exceptions": con.execute("select * from analytics.mart_routing_exception order by decision_id").df(),
        "evaluation": con.execute("""select * from analytics.mart_routing_evaluation
                                     order by policy, metric_id, period_scope""").df(),
        "weights": con.execute("select * from reference.lead_priority_weight_config order by config_id").df(),
        "pref_weights": con.execute("select * from reference.routing_preference_weight_config order by preference_id").df(),
        "registry": con.execute("select * from reference.lead_priority_component_registry order by component_id").df(),
        "bands": con.execute("select * from reference.lead_priority_band_rules order by band_code").df(),
        "queues": con.execute("select * from reference.routing_queue_rules order by queue_priority").df(),
        "thresholds": con.execute("select * from reference.routing_evaluation_thresholds order by parameter").df(),
        "rms": con.execute("select rm_id from analytics.dim_relationship_manager order by rm_id").df(),
    }
    con.close()
    return d


# ------------------------------------------------------------------ business-facing result files
def analyse(d):
    p, c, r, e = d["priority"], d["candidates"], d["recommendation"], d["exceptions"]
    comp = d["components"]
    out = {}

    # one row per decision: the business-facing priority result, without any fictional contact field
    out["lead_priority_results"] = p[[
        "decision_id", "lead_id", "as_of_timestamp", "as_of_date", "decision_trigger", "decision_type",
        "lead_status_at_decision", "is_capacity_warm_up_period", "source_priority_band", "priority_eligibility",
        "eligibility_rule_id", "eligibility_next_action", "eligibility_owner_role", "base_priority_score",
        "base_priority_band", "recommended_priority_score", "recommended_priority_band",
        "recommended_priority_band_name", "recommended_first_contact_sla_id", "recommended_operational_expectation",
        "override_rule_id", "override_action", "override_reason", "business_value_contribution",
        "operational_urgency_contribution", "first_channel_code", "stated_product_interest", "employee_band",
        "size_segment", "industry_code", "specialist_requirement_level", "territory_id", "cma_code_sgc2021",
        "market_context_treatment", "evidence_tier", "completeness_items_present", "lead_age_hours_at_decision",
        "pre_decision_engagement_state", "rule_version"]].copy()

    # component summary: how much each component contributes, and how often it falls back to its neutral value
    cs = comp.groupby(["pillar", "component_id", "component_name"], as_index=False).agg(
        decisions=("decision_id", "count"),
        effective_weight=("effective_weight", "max"),
        mean_normalized_value=("normalized_value", "mean"),
        min_normalized_value=("normalized_value", "min"),
        max_normalized_value=("normalized_value", "max"),
        mean_contribution=("contribution", "mean"),
        share_using_neutral_default=("used_neutral_default", "mean"))
    total_mean = cs.mean_contribution.sum()
    cs["share_of_mean_score"] = (cs.mean_contribution / total_mean).round(6)
    # how much of the cross-lead variance in the score each component explains, on its own scale
    var = comp.pivot(index="decision_id", columns="component_id", values="contribution")
    tot_var = var.sum(axis=1).var()
    cs["share_of_score_variance"] = cs.component_id.map(
        {k: round(float(var[k].var() / tot_var), 6) for k in var.columns})
    out["priority_component_summary"] = cs.round(4)

    # band profile
    scored = p[p.recommended_priority_band.notna()]
    bp = scored.groupby("recommended_priority_band", as_index=False).agg(
        decisions=("decision_id", "count"),
        min_score=("recommended_priority_score", "min"),
        median_score=("recommended_priority_score", "median"),
        max_score=("recommended_priority_score", "max"),
        share_from_override=("override_rule_id", lambda v: float(v.notna().mean())),
        mean_business_value=("business_value_contribution", "mean"),
        mean_operational_urgency=("operational_urgency_contribution", "mean"))
    bp["share_of_scored_decisions"] = (bp.decisions / len(scored)).round(6)
    by_type = (scored.groupby(["recommended_priority_band", "decision_type"]).size()
               .unstack(fill_value=0).add_prefix("decisions_"))
    out["priority_band_profile"] = bp.merge(by_type, on="recommended_priority_band", how="left").round(4)

    # routing recommendations, business-facing
    out["routing_recommendations"] = r[[
        "decision_id", "lead_id", "as_of_timestamp", "as_of_date", "decision_trigger", "decision_type",
        "is_capacity_warm_up_period", "territory_id", "territory_type", "cma_code_sgc2021", "size_segment",
        "industry_code", "specialist_requirement_level", "recommended_priority_score", "recommended_priority_band",
        "recommendation_outcome", "recommended_rm_id", "recommended_eligibility_stage", "recommended_via_national_pool",
        "recommended_preference_score", "eligible_candidate_count", "separating_tiebreak_id",
        "recommended_territory_coverage_role", "recommended_rm_holds_primary_specialization",
        "recommended_capacity_snapshot_id", "recommended_capacity_snapshot_as_of_utc", "recommended_utilization_ratio",
        "recommended_remaining_capacity_points", "recommended_capacity_warning", "recommended_queue_code",
        "recommended_queue_name", "queue_owner_role", "queue_next_action", "recommendation_reason",
        "historical_selected_rm_id", "same_rm_as_historical", "rule_version"]].copy()

    # candidate summary: per decision, how the pool narrowed
    # Negate the rule flags once, then aggregate with sums: a per-group Python lambda over 18,000 groups is the
    # slowest thing in this script and produces exactly the same integers.
    fails = {"failed_employment": "passes_rel_06_employment", "failed_availability": "passes_rel_07_availability",
             "failed_territory": "passes_rel_08_territory", "failed_pool": "passes_rel_09_pool",
             "failed_specialist": "passes_rel_10_specialist", "failed_language": "passes_rel_11_language",
             "failed_snapshot": "passes_rel_12_snapshot", "failed_hard_limit": "passes_rel_13_hard_limit",
             "failed_exclusion": "passes_rel_14_exclusion"}
    cc = c[["decision_id", "rm_id", "is_eligible", "utilization_ratio_at_decision"]].copy()
    for name, col in fails.items():
        # REL-13 is three-valued: a rule that could not be evaluated is not a failure
        cc[name] = (~c[col].fillna(True).astype(bool)).astype("int64")
    pool = cc.groupby("decision_id", as_index=False).agg(
        **{"candidates_evaluated": ("rm_id", "count"), "eligible": ("is_eligible", "sum")},
        **{name: (name, "sum") for name in fails},
        **{"min_utilization": ("utilization_ratio_at_decision", "min"),
           "max_utilization": ("utilization_ratio_at_decision", "max")})
    rule_rows = []
    for col, rule, name in [("passes_rel_06_employment", "REL-06", "RM employment status"),
                            ("passes_rel_07_availability", "REL-07", "RM availability at the decision"),
                            ("passes_rel_08_territory", "REL-08", "Effective territory coverage"),
                            ("passes_rel_09_pool", "REL-09", "Remote and national coverage"),
                            ("passes_rel_10_specialist", "REL-10", "Mandatory specialist requirement"),
                            ("passes_rel_11_language", "REL-11", "Explicit service-language requirement"),
                            ("passes_rel_12_snapshot", "REL-12", "Capacity snapshot availability"),
                            ("passes_rel_13_hard_limit", "REL-13", "Capacity hard limit"),
                            ("passes_rel_14_exclusion", "REL-14", "Prior-owner exclusion")]:
        rule_rows.append({"rule_id": rule, "rule_name": name,
                          "candidates_evaluated": int(len(c)),
                          "candidates_failing": int((~c[col].fillna(True).astype(bool)).sum()),
                          "share_failing": round(float((~c[col].fillna(True).astype(bool)).mean()), 6),
                          "candidates_not_evaluated": int(c[col].isna().sum()),
                          "decisions_with_at_least_one_failure":
                              int(c[~c[col].fillna(True).astype(bool)].decision_id.nunique())})
    out["routing_candidate_summary"] = pd.DataFrame(rule_rows)
    out["routing_candidate_pool"] = pool.round(6)

    # exceptions
    out["routing_exceptions"] = e[[
        "decision_id", "lead_id", "as_of_timestamp", "as_of_date", "decision_type", "is_capacity_warm_up_period",
        "territory_id", "specialist_requirement_level", "priority_eligibility", "recommended_priority_band",
        "queue_source", "queue_code", "queue_name", "queue_reason", "failed_rule_codes", "queue_owner_role",
        "queue_sla_id", "queue_sla_target", "next_action", "escalation_path", "candidates_evaluated",
        "candidates_passing_territory", "candidates_passing_specialist", "candidates_passing_hard_limit",
        "candidates_at_or_above_hard_limit", "requires_manual_review", "rule_version"]].copy()
    qr = e.groupby(["queue_code", "queue_name", "queue_owner_role", "queue_sla_id"], as_index=False).agg(
        decisions=("decision_id", "count"))
    qr["share_of_all_decisions"] = (qr.decisions / len(r)).round(6)
    out["routing_queue_reasons"] = qr.sort_values("decisions", ascending=False).reset_index(drop=True)

    # the capacity-aware operational recommendation, with the full ledger
    op = d["operational"]
    out["operational_recommendations"] = op[[
        "decision_id", "lead_id", "as_of_timestamp", "as_of_date", "processing_sequence", "decision_type",
        "priority_tier", "priority_score", "max_sla_pressure_at_decision", "recommendation_status",
        "recommended_rm_id", "candidate_rank", "preference_score", "eligible_candidate_count",
        "candidates_rejected_for_capacity", "queue_code", "queue_name", "queue_reason", "queue_owner_role",
        "queue_next_action", "capacity_snapshot_id", "capacity_snapshot_as_of_utc",
        "observed_workload_points_at_snapshot", "simulated_workload_added_since_snapshot",
        "workload_points_added_by_this_recommendation", "simulated_post_assignment_workload_points",
        "capacity_limit_points", "simulated_post_assignment_utilization", "remaining_capacity_points_after",
        "territory_eligibility_result", "specialist_eligibility_result", "language_eligibility_result",
        "separating_tiebreak_id", "historical_selected_rm_id", "same_rm_as_historical", "rule_version"]].copy()

    moved = op.merge(d["recommendation"][["decision_id", "recommended_rm_id"]].rename(
        columns={"recommended_rm_id": "static_rm_id"}), on="decision_id", how="left")
    moved = moved[moved.recommended_rm_id.ne(moved.static_rm_id)]
    out["capacity_ledger_effect"] = pd.DataFrame([{
        "decisions": int(len(op)),
        "assigned": int((op.recommendation_status == "assigned").sum()),
        "queued": int((op.recommendation_status == "queued").sum()),
        "decisions_where_the_ledger_changed_the_rm": int(len(moved)),
        "decisions_where_a_candidate_was_skipped_for_capacity": int((op.candidates_rejected_for_capacity > 0).sum()),
        "max_simulated_workload_added_within_a_snapshot_window": float(op.simulated_workload_added_since_snapshot.max()),
        "max_simulated_post_assignment_utilization": float(op.simulated_post_assignment_utilization.max()),
        "assignment_workload_points": float(op.assignment_workload_points_declared.max()),
        "interpretation": ("The ledger consumes capacity across sequential decisions, so an RM that has already been "
                           "given work has less room for the next lead. It binds within a capacity-snapshot window; "
                           "each new observed snapshot replaces the basis, because that snapshot is the evidence of "
                           "what the RM is really carrying."),
    }])

    # policy comparison (from the published evaluation mart, all three policies)
    ev = d["evaluation"]
    out["policy_comparison"] = ev[["policy", "metric_id", "metric_name", "interpretation", "period_scope",
                                   "numerator", "denominator", "metric_value", "rule_version"]].copy()

    # same-RM rate by channel: RT-E05 self-assignment is not modelled, so CH-RMP is expected to differ most
    chan = r.merge(p[["decision_id", "first_channel_code"]], on="decision_id", how="left")
    chan = chan[(chan.recommendation_outcome == "rm_assignment") & chan.historical_selected_rm_id.notna()]
    out["same_rm_rate_by_channel"] = (chan.groupby("first_channel_code", as_index=False)
                                      .agg(comparable_decisions=("decision_id", "count"),
                                           same_rm=("same_rm_as_historical", "sum"))
                                      .assign(same_rm_rate=lambda x: (x.same_rm / x.comparable_decisions).round(6)))

    # specialist demand and gap, mandatory and preferred kept separate
    sp = []
    for lvl in ["mandatory", "preferred"]:
        sub = r[r.specialist_requirement_level == lvl]
        assigned = sub[sub.recommendation_outcome == "rm_assignment"]
        sp.append({
            "requirement_level": lvl,
            "population_scope": ("all decisions; the matched-historical-population rates used in the policy "
                                 "comparison are in mart_routing_evaluation and policy_comparison.csv"),
            "demand_decisions": int(len(sub)),
            "demand_share_of_all_decisions": round(float(len(sub) / len(r)), 6),
            "recommendations_made": int(len(assigned)),
            "recommended_specialist_met": int(assigned.recommended_rm_holds_primary_specialization.sum() if lvl == "mandatory"
                                              else assigned.recommended_rm_holds_any_specialization.sum()),
            # compliance is measured over the recommendations actually made; fulfilment over all demand, including the
            # decisions held in a queue because no specialist was available
            "recommended_compliance_rate": round(float(
                (assigned.recommended_rm_holds_primary_specialization if lvl == "mandatory"
                 else assigned.recommended_rm_holds_any_specialization).mean()) if len(assigned) else np.nan, 6),
            "recommended_fulfilment_rate": round(float(
                (assigned.recommended_rm_holds_primary_specialization.sum() if lvl == "mandatory"
                 else assigned.recommended_rm_holds_any_specialization.sum()) / len(sub)) if len(sub) else np.nan, 6),
            "historical_specialist_met": int(sub.historical_rm_holds_primary_specialization.fillna(False).sum() if lvl == "mandatory"
                                             else sub.historical_rm_holds_any_specialization.fillna(False).sum()),
            "historical_fulfilment_rate": round(float(
                (sub.historical_rm_holds_primary_specialization if lvl == "mandatory"
                 else sub.historical_rm_holds_any_specialization).fillna(False).mean()) if len(sub) else np.nan, 6),
            "national_pool_use": int(sub.recommended_via_national_pool.fillna(False).sum()),
            "generalist_fallback": int((assigned.recommended_rm_holds_any_specialization == False).sum()),  # noqa: E712
            "no_specialist_available_queue": int((sub.recommended_queue_code == "Q-NSP").sum()),
            "unmet_demand": int((sub.recommended_queue_code == "Q-NSP").sum()) if lvl == "mandatory" else 0,
            "unmet_demand_note": ("A mandatory-specialist lead with no eligible regional or national specialist is "
                                  "held in Q-NSP; it is never given to a generalist."
                                  if lvl == "mandatory" else
                                  "A preferred-specialist lead is never held: it goes to a generalist, which is "
                                  "counted as generalist fallback rather than unmet demand."),
        })
    out["specialist_demand_gap"] = pd.DataFrame(sp)
    # by industry
    ind = r[r.specialist_requirement_level.isin(["mandatory", "preferred"])].groupby(
        ["specialist_requirement_level", "industry_code"], as_index=False).agg(
        decisions=("decision_id", "count"),
        recommended_to_a_specialist=("recommended_rm_holds_any_specialization", "sum"),
        queued=("recommended_queue_code", lambda v: int(v.notna().sum())))
    ind["recommended_specialist_rate"] = (ind.recommended_to_a_specialist / ind.decisions).round(6)
    out["specialist_demand_by_industry"] = ind

    # capacity profile at recommendation, and the pile-up diagnostic
    a = r[r.recommendation_outcome == "rm_assignment"].copy()
    cp = a.groupby("is_capacity_warm_up_period", as_index=False).agg(
        recommendations=("decision_id", "count"),
        mean_utilization=("recommended_utilization_ratio", "mean"),
        p50_utilization=("recommended_utilization_ratio", "median"),
        p90_utilization=("recommended_utilization_ratio", lambda v: float(np.quantile(v, 0.9))),
        p99_utilization=("recommended_utilization_ratio", lambda v: float(np.quantile(v, 0.99))),
        max_utilization=("recommended_utilization_ratio", "max"),
        at_or_above_warning=("recommended_capacity_warning", "sum"),
        mean_remaining_points=("recommended_remaining_capacity_points", "mean"))
    out["capacity_profile_at_recommendation"] = cp.round(6)

    # Pile-up: the static counterfactual never consumes capacity, so count how many recommendations the policy would
    # have sent to the same RM on the same snapshot day, and compare with that RM's remaining workload points.
    pile = a.groupby(["recommended_rm_id", "as_of_date"], as_index=False).agg(
        recommendations_that_day=("decision_id", "count"),
        remaining_points_at_first_decision=("recommended_remaining_capacity_points", "max"),
        capacity_limit_points=("recommended_capacity_limit_points", "max"))
    pile["would_exceed_remaining_points"] = pile.recommendations_that_day > pile.remaining_points_at_first_decision
    out["capacity_pile_up_diagnostic"] = pile.sort_values(
        ["recommendations_that_day", "recommended_rm_id", "as_of_date"], ascending=[False, True, True]).head(500)
    out["capacity_pile_up_summary"] = pd.DataFrame([{
        "rm_days_with_a_recommendation": int(len(pile)),
        "max_recommendations_to_one_rm_in_one_day": int(pile.recommendations_that_day.max()),
        "mean_recommendations_per_rm_day": round(float(pile.recommendations_that_day.mean()), 4),
        "rm_days_where_the_days_recommendations_exceed_the_remaining_points": int(pile.would_exceed_remaining_points.sum()),
        "share_of_rm_days_exceeding_remaining_points": round(float(pile.would_exceed_remaining_points.mean()), 6),
        "interpretation": ("A recommendation never consumes capacity in this framework, so each decision sees the "
                           "capacity the historical policy produced. This diagnostic counts how often a day's "
                           "recommendations would have outgrown the RM's remaining points if they had been acted on, "
                           "which is the exposure the static comparison cannot show."),
    }])

    # workload distribution for both policies
    # Reindexed over every RM, so an RM that received no work counts as a zero rather than dropping out of the
    # distribution and flattering the concentration measures.
    all_rms = d["rms"].rm_id
    rec_load = a.recommended_rm_id.value_counts().reindex(all_rms, fill_value=0).rename("recommended").to_frame()
    hist_load = r.historical_selected_rm_id.dropna().value_counts().reindex(all_rms, fill_value=0).rename("historical").to_frame()
    load = rec_load.join(hist_load, how="outer").fillna(0).astype(int)
    load.index.name = "rm_id"
    out["workload_distribution"] = load.reset_index().sort_values("rm_id").reset_index(drop=True)
    return out


def gini(v):
    v = np.sort(np.asarray(v, dtype=float))
    n = len(v)
    if n == 0 or v.sum() == 0:
        return np.nan
    return float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum()))


# ------------------------------------------------------------------ sensitivity over the published values
def sensitivity(d):
    comp = d["components"]
    wide = comp.pivot(index="decision_id", columns="component_id", values="normalized_value")
    eff = comp.drop_duplicates("component_id").set_index("component_id")
    w = (eff.pillar_weight * eff.component_weight)
    cols = list(w.index)
    bands = d["bands"].copy()
    bands[["min_score_inclusive", "max_score_exclusive"]] = bands[["min_score_inclusive", "max_score_exclusive"]].astype(float)

    # Vectorized banding. The declared bands do not overlap (a dbt test asserts it), so assigning each band over the
    # whole column gives exactly what a per-row scan gives, without 18,000 Python calls per perturbation.
    def band_of(series):
        out = pd.Series(np.full(len(series), None, dtype=object), index=series.index)
        for r in bands.itertuples():
            out[(series >= r.min_score_inclusive) & (series < r.max_score_exclusive)] = r.band_code
        return out

    base = (wide[cols] * w[cols].values).sum(axis=1)
    base_band = band_of(base)
    rows = []
    for c in cols:
        for delta in (-0.05, -0.02, 0.02, 0.05):
            w2 = w.copy()
            w2[c] = max(0.0, w2[c] + delta)
            w2 = w2 / w2.sum()
            s2 = (wide[cols] * w2[cols].values).sum(axis=1)
            b2 = band_of(s2)
            rows.append({"perturbed": c, "effective_weight_delta": delta,
                         "renormalized_weight": round(float(w2[c]), 6),
                         "mean_absolute_score_change": round(float((s2 - base).abs().mean()), 4),
                         "max_absolute_score_change": round(float((s2 - base).abs().max()), 4),
                         "band_changes": int((base_band != b2).sum()),
                         "band_change_share": round(float((base_band != b2).mean()), 6),
                         "spearman_with_base": round(float(pd.Series(base).rank().corr(pd.Series(s2).rank())), 6)})
    # leave-one-component-out
    for c in cols:
        w2 = w.copy()
        w2[c] = 0.0
        w2 = w2 / w2.sum()
        s2 = (wide[cols] * w2[cols].values).sum(axis=1)
        b2 = band_of(s2)
        rows.append({"perturbed": c, "effective_weight_delta": "leave out",
                     "renormalized_weight": 0.0,
                     "mean_absolute_score_change": round(float((s2 - base).abs().mean()), 4),
                     "max_absolute_score_change": round(float((s2 - base).abs().max()), 4),
                     "band_changes": int((base_band != b2).sum()),
                     "band_change_share": round(float((base_band != b2).mean()), 6),
                     "spearman_with_base": round(float(pd.Series(base).rank().corr(pd.Series(s2).rank())), 6)})
    pri = pd.DataFrame(rows)

    # recommendation stability under preference-weight perturbation
    c = d["candidates"]
    pw = d["pref_weights"].set_index("preference_id")["weight"].astype(float)
    ncols = ["n01_territory_role", "n02_specialist_fit", "n03_segment_fit", "n04_continuity",
             "n05_headroom", "n06_balance", "n07_language_fit"]
    # Only the columns the declared ranking needs: copying the full candidate frame once per perturbation dominates
    # the runtime and changes nothing.
    keep = ["decision_id", "rm_id", "candidate_rank", "utilization_ratio_at_decision",
            "remaining_capacity_points"] + ncols
    el = c.loc[c.is_eligible, keep].copy()
    base_winner = el[el.candidate_rank == 1][["decision_id", "rm_id"]].rename(columns={"rm_id": "base_rm"})
    # The declared tie-break order, applied with numpy. factorize(sort=True) numbers the uniques in lexicographic
    # order, so comparing the integer codes is identical to comparing the identifier strings, and np.lexsort with the
    # primary key last reproduces the same ordering as sort_values on the five declared columns.
    dec_code = pd.factorize(el.decision_id, sort=True)[0]
    rm_code = pd.factorize(el.rm_id, sort=True)[0]
    util = el.utilization_ratio_at_decision.to_numpy(dtype=float)
    remain = el.remaining_capacity_points.to_numpy(dtype=float)
    nvals = [el[c_].to_numpy(dtype=float) for c_ in ncols]
    rm_vals = el.rm_id.to_numpy()
    base_rm_by_dec = base_winner.set_index("decision_id").base_rm
    dec_vals = el.decision_id.to_numpy()
    rows = []
    for pid, ncol in zip(pw.index, ncols):
        for delta in (-0.05, -0.03, 0.03, 0.05):
            w2 = pw.copy()
            w2[pid] = max(0.0, w2[pid] + delta)
            w2 = w2 / w2.sum()
            sc = np.round(sum(w2.iloc[i] * nvals[i] for i in range(len(ncols))), 6)
            order = np.lexsort((rm_code, -remain, util, -sc, dec_code))
            d_sorted = dec_code[order]
            first = np.empty(len(d_sorted), dtype=bool)
            first[0] = True
            np.not_equal(d_sorted[1:], d_sorted[:-1], out=first[1:])
            win_idx = order[first]
            win = pd.DataFrame({"decision_id": dec_vals[win_idx], "rm_id": rm_vals[win_idx]})
            mg = base_winner.merge(win, on="decision_id")
            rows.append({"perturbed": pid, "weight_delta": delta, "renormalized_weight": round(float(w2[pid]), 6),
                         "decisions": int(len(mg)),
                         "recommendation_changes": int((mg.base_rm != mg.rm_id).sum()),
                         "recommendation_change_share": round(float((mg.base_rm != mg.rm_id).mean()), 6)})
    pref = pd.DataFrame(rows)
    return {"priority_sensitivity": pri, "recommendation_stability": pref,
            "base_score": base, "base_band": base_band}


# ------------------------------------------------------------------ worked examples, chosen deterministically
def worked_examples(d):
    p, r, c, comp = d["priority"], d["recommendation"], d["candidates"], d["components"]
    rows = []

    def pick(df, rule):
        df = df.sort_values("decision_id")
        return df.iloc[0] if len(df) else None

    # one lead per band: the lowest decision_id in the band, so the choice never depends on an outcome
    for band in ["P1", "P2", "P3"]:
        sel = pick(p[(p.recommended_priority_band == band) & (p.override_rule_id.isna())], band)
        if sel is None:
            sel = pick(p[p.recommended_priority_band == band], band)
        if sel is not None:
            rows.append({"example": f"{band} lead", "selection_rule": f"lowest decision_id with recommended band {band}",
                         "decision_id": sel.decision_id, "detail": (
                             f"score {sel.recommended_priority_score}, band {sel.recommended_priority_band}, "
                             f"business value {sel.business_value_contribution}, operational urgency "
                             f"{sel.operational_urgency_contribution}, override {sel.override_rule_id or 'none'}")})
    examples = {
        "RM recommendation": r[(r.recommendation_outcome == "rm_assignment") & (r.decision_type == "initial")],
        "queue outcome": r[r.recommendation_outcome == "queue"],
        "mandatory-specialist decision": r[(r.specialist_requirement_level == "mandatory")
                                           & (r.recommendation_outcome == "rm_assignment")],
        "national-pool fallback": r[r.recommended_via_national_pool == True],  # noqa: E712
        "reassignment": r[(r.decision_type == "reassignment") & (r.recommendation_outcome == "rm_assignment")],
    }
    for name, df in examples.items():
        sel = pick(df, name)
        if sel is not None:
            rows.append({"example": name, "selection_rule": f"lowest decision_id matching: {name}",
                         "decision_id": sel.decision_id, "detail": str(sel.recommendation_reason)[:600]})
    # a capacity-excluded candidate: the lowest (decision_id, rm_id) failing only the hard limit
    ce = c[(~c.passes_rel_13_hard_limit) & c.passes_rel_06_employment & c.passes_rel_07_availability
           & c.passes_rel_08_territory & c.passes_rel_09_pool & c.passes_rel_10_specialist & c.passes_rel_11_language
           & c.passes_rel_12_snapshot & c.passes_rel_14_exclusion
           & c.utilization_ratio_at_decision.notna()].sort_values(["decision_id", "rm_id"])
    if len(ce):
        s = ce.iloc[0]
        rows.append({"example": "capacity-excluded candidate",
                     "selection_rule": "lowest (decision_id, rm_id) failing only REL-13, with an observed utilization",
                     "decision_id": f"{s.decision_id} / {s.rm_id}",
                     "detail": (f"utilization {s.utilization_ratio_at_decision} at or above the hard limit in "
                                f"snapshot {s.capacity_snapshot_id} ({s.capacity_snapshot_as_of_utc}); "
                                f"failed rules {s.failed_rule_codes}")})
    ex = pd.DataFrame(rows)
    # the component breakdown of the three band examples, for the documentation
    ids = [x for x in ex[ex.example.str.endswith(" lead")].decision_id]
    br = comp[comp.decision_id.isin(ids)][
        ["decision_id", "component_id", "pillar", "raw_value_text", "raw_value_numeric", "transformation",
         "normalized_value", "direction", "used_neutral_default", "pillar_weight", "component_weight",
         "effective_weight", "contribution"]].sort_values(["decision_id", "component_id"])
    return ex, br


# ------------------------------------------------------------------ findings
def findings(d, out, sens):
    p, r, ev = d["priority"], d["recommendation"], d["evaluation"]
    pile = out["capacity_pile_up_summary"]
    wl = out["workload_distribution"]
    op = d["operational"]
    led = out["capacity_ledger_effect"].iloc[0]

    def m(policy, metric, scope="full_period", field="metric_value"):
        row = ev[(ev.policy == policy) & (ev.metric_id == metric) & (ev.period_scope == scope)]
        if row.empty or pd.isna(row[field].iloc[0]):
            return None
        return float(row[field].iloc[0])

    scored = p[p.recommended_priority_band.notna()]
    rec_load = wl.set_index("rm_id").recommended
    hist_load = wl.set_index("rm_id").historical
    f = [
        ("F-01", "Decision population",
         f"{len(p):,} routing decisions on {p.lead_id.nunique():,} leads: {int((p.decision_trigger == 'historical_routing_decision').sum()):,} "
         f"replayed from the synthetic routing log and {int((p.decision_trigger == 'intake_routability_review').sum()):,} "
         f"intake-routability reviews for leads the routing engine never saw.", "rule-adherence comparison"),
        ("F-02", "Priority eligibility",
         f"{int((p.priority_eligibility == 'score_eligible').sum()):,} decisions are score eligible; "
         f"{int((p.priority_eligibility != 'score_eligible').sum()):,} are gated before any score exists "
         f"({', '.join(f'{k} {v}' for k, v in p[p.priority_eligibility != 'score_eligible'].priority_eligibility.value_counts().items())}). "
         "Three declared outcomes (withdrawn, terminal, not_yet_routable) are defined and tested but never observed, "
         "because the synthetic routing log takes no decision on a lead in those states.", "rule-adherence comparison"),
        ("F-03", "Priority band distribution",
         f"P1 {int((scored.recommended_priority_band == 'P1').sum()):,}, P2 {int((scored.recommended_priority_band == 'P2').sum()):,}, "
         f"P3 {int((scored.recommended_priority_band == 'P3').sum()):,}. Scores run from "
         f"{scored.recommended_priority_score.min():.2f} to {scored.recommended_priority_score.max():.2f}.",
         "rule-adherence comparison"),
        ("F-04", "P1 is produced mainly by declared overrides, not by the base score",
         f"{int((scored.base_priority_band == 'P1').sum()):,} decisions reach the P1 floor on their base score alone; "
         f"{int(((scored.recommended_priority_band == 'P1') & scored.override_rule_id.notna()).sum()):,} reach P1 through an "
         "override rule, overwhelmingly POV-01 (an in-flight clock at or past 90 percent of its target). At an initial "
         "routing decision the operational-urgency pillar carries almost no signal, because the synthetic process "
         "routes within minutes of intake, so business-value evidence alone rarely clears 65.", "rule-adherence comparison"),
        ("F-05", "Recommendation coverage",
         f"On the matched historical population the framework produces an RM recommendation for "
         f"{m('static_diagnostic', 'assignment_rate'):.2%} of decisions under the static diagnostic and "
         f"{m('capacity_aware_operational', 'assignment_rate'):.2%} under the capacity-aware operational policy; "
         "the rest end in a controlled queue.",
         "rule-adherence comparison"),
        ("F-06", "The recommended and historical policies agree on the RM about a quarter of the time",
         f"The operational recommendation matches the historical RM on "
         f"{float(op.same_rm_as_historical.fillna(False).mean()):.2%} of decisions. The two policies optimise different things: "
         "the historical simulation balances load with its own tie-break, this framework ranks on seven declared "
         "preferences. A low agreement rate is a measure of policy difference, not of error in either policy.",
         "routing-policy counterfactual"),
        ("F-07", "Mandatory-specialist handling: compliance is absolute, fulfilment is higher than historical",
         f"Compliance, meaning the share of mandatory-specialist decisions that were recommended to an RM and went to "
         f"a holder of that primary specialization, is "
         f"{int(r[(r.specialist_requirement_level == 'mandatory') & (r.recommendation_outcome == 'rm_assignment') & r.recommended_rm_holds_primary_specialization].shape[0]):,} "
         f"of {int(r[(r.specialist_requirement_level == 'mandatory') & (r.recommendation_outcome == 'rm_assignment')].shape[0]):,}, "
         "that is 100 percent, because REL-10 is an eligibility rule rather than a preference. Fulfilment, meaning the "
         f"share of all mandatory-specialist decisions that reached a specialist, is "
         f"{m('capacity_aware_operational', 'mandatory_specialist_fulfilment'):.2%} against historical "
         f"{m('historical', 'mandatory_specialist_fulfilment'):.2%}; the gap to 100 percent is the leads held in Q-NSP "
         "because no specialist was available at all.",
         "rule-adherence comparison"),
        ("F-08", "Preferred-specialist fulfilment is lower under the declared rules",
         f"Recommended {m('capacity_aware_operational', 'preferred_specialist_fulfilment'):.2%} against historical "
         f"{m('historical', 'preferred_specialist_fulfilment'):.2%}. RPF-02 weights specialist fit at 0.18 while capacity "
         "headroom and workload balance together carry 0.32, so a preferred-specialist lead often goes to a "
         "lighter-loaded generalist. This is a declared trade-off, reported as it stands and not tuned away.",
         "assignment-quality proxy"),
        ("F-09", "Hard-capacity and overload exposure",
         f"Hard-capacity breaches: operational {m('capacity_aware_operational', 'hard_capacity_breaches'):.4%} against "
         f"historical {m('historical', 'hard_capacity_breaches'):.4%}. REL-13 makes the hard limit an eligibility rule and "
         f"the ledger enforces it again after the assignment, so the "
         "difference is adherence to that rule given the capacity the historical policy produced. A recommendation "
         "never consumes capacity here, so this is not a forecast of what this policy's own loads would be.",
         "operational exposure"),
        ("F-10", "Workload concentration is far worse under the recommended policy, and this is the clearest "
                 "consequence of the static counterfactual",
         f"The busiest decile of RMs receives "
         f"{m('capacity_aware_operational', 'top_decile_share'):.2%} "
         f"of recommendations against "
         f"{m('historical', 'top_decile_share'):.2%} "
         f"historically; Gini {gini(rec_load.values):.3f} against {gini(hist_load.values):.3f}. One RM receives "
         f"{int(rec_load.max()):,} recommendations where the historical maximum is {int(hist_load.max()):,}, and "
         f"{int((rec_load == 0).sum())} of {int(len(rec_load))} RMs receive none at all. This is driven by the static "
         "counterfactual: a recommendation never consumes capacity, so the same lightly loaded RM keeps winning RPF-05 "
         "and RPF-06 at every decision in its territory. What the distribution would look like under a live capacity "
         "ledger that each recommendation decrements is untested here, and any operational use would need one. "
         "Nothing in this evidence supports a claim that the declared rules balance workload better than the "
         "historical policy; on this evidence they concentrate it.", "operational exposure"),
        ("F-10B", "Pile-up diagnostic",
         f"Across {int(pile['rm_days_with_a_recommendation'].iloc[0]):,} RM business days with at least one "
         f"recommendation, the mean is {float(pile['mean_recommendations_per_rm_day'].iloc[0]):.2f} and the maximum "
         f"{int(pile['max_recommendations_to_one_rm_in_one_day'].iloc[0])} in a single day. On "
         f"{int(pile['rm_days_where_the_days_recommendations_exceed_the_remaining_points'].iloc[0])} RM days "
         f"({float(pile['share_of_rm_days_exceeding_remaining_points'].iloc[0]):.3%}) the day's recommendations would "
         "already have outgrown that RM's remaining workload points if they had been acted on. That share is small "
         "only because utilization is low throughout this synthetic year; it is the quantity a live implementation "
         "would have to control.", "operational exposure"),
        ("F-11", "Queue outcomes",
         f"{int((r.recommendation_outcome == 'queue').sum()):,} decisions end in a controlled queue: "
         f"{', '.join(f'{k} {v_}' for k, v_ in r.recommended_queue_code.value_counts().items())}. "
         + ("Every declared queue is raised at least once."
            if not sorted(set(d["queues"].queue_code) - set(r.recommended_queue_code.dropna()))
            else "Declared but never raised in this baseline: "
                 + ", ".join(sorted(set(d["queues"].queue_code) - set(r.recommended_queue_code.dropna())))
                 + "; they remain defined, documented and tested.") , "operational exposure"),
        ("F-12", "The national specialist pool carries real demand",
         f"{int(r.recommended_via_national_pool.fillna(False).sum()):,} mandatory-specialist decisions are served by the "
         "TER-10 national fallback because no regional specialist is eligible, and "
         f"{int((r.recommended_queue_code == 'Q-NSP').sum()):,} still find no specialist at all. Specialist staffing is "
         "unchanged by this work; this measures the gap, it does not close it.", "operational exposure"),
        ("F-13", "Capacity warm-up",
         f"{int(p.is_capacity_warm_up_period.sum()):,} decisions fall in the declared warm-up quarter "
         f"(2025-06-01 to 2025-09-01), when every RM starts at zero workload. Excluding it, hard-limit exposure is "
         f"{m('capacity_aware_operational', 'hard_capacity_breaches', 'excluding_warm_up'):.4%} operational against "
         f"{m('historical', 'hard_capacity_breaches', 'excluding_warm_up'):.4%} historical. Early observations are "
         "flagged and reported, never deleted.", "operational exposure"),
        ("F-14", "Score sensitivity",
         f"No single component weight moved by 0.05 and renormalized changes more than "
         f"{sens['priority_sensitivity'][sens['priority_sensitivity'].effective_weight_delta != 'leave out'].band_change_share.max():.2%} "
         f"of base bands; removing a component entirely changes at most "
         f"{sens['priority_sensitivity'][sens['priority_sensitivity'].effective_weight_delta == 'leave out'].band_change_share.max():.2%}. "
         "The ranking is robust to small changes in the declared weights.", "rule-adherence comparison"),
        ("F-15", "Recommendation stability",
         f"Moving a preference weight by 0.05 and renormalizing changes at most "
         f"{sens['recommendation_stability'].recommendation_change_share.max():.2%} of recommended RMs, and "
         f"{float(d['recommendation'].tiebreak_beyond_preference_applied.fillna(False).mean()):.2%} of static "
         "recommendations are separated by a tie-break below the "
         "preference score, where the stable RM identifier is the last resort.", "rule-adherence comparison"),
        ("F-16", "Channel self-assignment and the named preferred RM are not modelled",
         "RT-E05 lets an RM-prospected lead self-assign to the prospecting RM, and RT-P05 honours a referrer's named "
         "preferred RM. Neither rule is implemented here, because neither input is available as an as-of field on the "
         "synthetic lead. Agreement with the historical policy by channel is "
         + ", ".join(f"{t.first_channel_code} {t.same_rm_rate:.2%}" for t in
                     out["same_rm_rate_by_channel"].sort_values("same_rm_rate").itertuples())
         + ". The spread is narrow, so the missing rules do not explain the overall difference between the two "
           "policies; the preference ranking does.", "routing-policy counterfactual"),
        ("F-17", "External market context cannot dominate the lead score",
         f"BV05 carries an effective weight of {float(d['components'][d['components'].component_id == 'BV05_MARKET'].effective_weight.iloc[0]):.3f}, "
         f"so at most {float(d['components'][d['components'].component_id == 'BV05_MARKET'].effective_weight.iloc[0]) * 100:.1f} "
         "score points. Tier 3 and missing-context leads take the declared neutral value 50, never zero: "
         f"{int(d['components'][(d['components'].component_id == 'BV05_MARKET') & d['components'].used_neutral_default].shape[0]):,} "
         "scored decisions use it.", "rule-adherence comparison"),
        ("F-18", "The source priority field is untouched",
         "Every synthetic lead carries source priority P2, so no comparison of recommended against source bands is "
         "possible. The source field is preserved unchanged and the recommendation is published beside it.",
         "rule-adherence comparison"),
    ]
    return pd.DataFrame([{"finding_id": a, "finding": b, "evidence": c, "interpretation": e} for a, b, c, e in f])


# ------------------------------------------------------------------ figures
def figures(d, out, sens):
    p, r, c = d["priority"], d["recommendation"], d["candidates"]
    scored = p[p.recommended_priority_band.notna()]

    def fig_bands():
        fig, ax = plt.subplots(figsize=(9, 5.5))
        order = ["P1", "P2", "P3"]
        base = [int((scored.base_priority_band == b).sum()) for b in order]
        final = [int((scored.recommended_priority_band == b).sum()) for b in order]
        x = np.arange(len(order))
        ax.bar(x - 0.2, base, 0.4, label="base score only", color="#7f9aa8")
        ax.bar(x + 0.2, final, 0.4, label="after declared overrides", color="#1f5f8b")
        for xi, (b, f_) in enumerate(zip(base, final)):
            ax.text(xi - 0.2, b, f"{b:,}", ha="center", va="bottom", fontsize=8)
            ax.text(xi + 0.2, f_, f"{f_:,}", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(x, order)
        ax.legend(fontsize=8)
        return fig, ax

    def fig_contrib():
        cs = out["priority_component_summary"].sort_values("mean_contribution")
        fig, ax = plt.subplots(figsize=(9, 6))
        colors = ["#1f5f8b" if v == "business_value" else "#e08a1e" for v in cs.pillar]
        ax.barh(cs.component_id, cs.mean_contribution, color=colors)
        for i, (v, s) in enumerate(zip(cs.mean_contribution, cs.share_of_score_variance)):
            ax.text(v, i, f"  {v:.2f} (var {s:.0%})", va="center", fontsize=7.5)
        ax.set_xlim(0, cs.mean_contribution.max() * 1.35)
        handles = [plt.Rectangle((0, 0), 1, 1, color="#1f5f8b"), plt.Rectangle((0, 0), 1, 1, color="#e08a1e")]
        ax.legend(handles, ["business value", "operational urgency"], fontsize=8, loc="lower right")
        return fig, ax

    def fig_outcomes():
        fig, ax = plt.subplots(figsize=(9, 5.5))
        v = r.groupby(["decision_type", "recommendation_outcome"]).size().unstack(fill_value=0)
        v = v.reindex(columns=["rm_assignment", "queue"], fill_value=0).sort_index()
        bottom = np.zeros(len(v))
        for col, color in [("rm_assignment", "#1f5f8b"), ("queue", "#e08a1e")]:
            ax.bar(v.index, v[col], bottom=bottom, label=col.replace("_", " "), color=color)
            bottom += v[col].values
        ax.legend(fontsize=8)
        ax.tick_params(axis="x", labelrotation=15)
        return fig, ax

    def fig_territory():
        """Rule compliance across all three policies, on the identical decision population."""
        fig, ax = plt.subplots(figsize=(9.5, 5))
        ev = d["evaluation"]
        mids = ["territory_rule_compliance", "mandatory_specialist_compliance", "language_fulfilment"]
        labels = ["territory rule", "mandatory specialist", "stated language"]
        pols = [("historical", "#e08a1e"), ("static_diagnostic", "#7f9aa8"), ("capacity_aware_operational", "#1f5f8b")]
        x = np.arange(len(mids))
        for i, (pol, colr) in enumerate(pols):
            vals = [float(ev[(ev.policy == pol) & (ev.metric_id == mid)
                             & (ev.period_scope == "full_period")].metric_value.iloc[0]) for mid in mids]
            ax.bar(x + (i - 1) * 0.27, vals, 0.27, label=pol.replace("_", " "), color=colr)
            for xi, vv in zip(x, vals):
                ax.text(xi + (i - 1) * 0.27, vv, f"{vv:.0%}", ha="center", va="bottom", fontsize=7.5)
        ax.set_xticks(x, labels)
        ax.set_ylim(0, 1.15)
        ax.legend(fontsize=8)
        return fig, ax

    def fig_specialist():
        fig, ax = plt.subplots(figsize=(9, 5))
        s = out["specialist_demand_gap"]
        x = np.arange(len(s))
        ax.bar(x - 0.2, s.recommended_fulfilment_rate, 0.4, label="recommended", color=POLICY_COLORS["recommended"])
        ax.bar(x + 0.2, s.historical_fulfilment_rate, 0.4, label="historical", color=POLICY_COLORS["historical"])
        for i, (a_, b_) in enumerate(zip(s.recommended_fulfilment_rate, s.historical_fulfilment_rate)):
            ax.text(i - 0.2, a_, f"{a_:.1%}", ha="center", va="bottom", fontsize=8)
            ax.text(i + 0.2, b_, f"{b_:.1%}", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(x, [f"{t} specialist\n({n:,} decisions)" for t, n in zip(s.requirement_level, s.demand_decisions)])
        ax.set_ylim(0, 1.15)
        ax.legend(fontsize=8)
        return fig, ax

    def fig_capacity():
        fig, ax = plt.subplots(figsize=(9, 5.5))
        a = r[r.recommendation_outcome == "rm_assignment"]
        bins = np.linspace(0, 1.0, 41)
        ax.hist(a[~a.is_capacity_warm_up_period].recommended_utilization_ratio, bins=bins,
                label="after the declared warm-up", color="#1f5f8b", alpha=0.85)
        ax.hist(a[a.is_capacity_warm_up_period].recommended_utilization_ratio, bins=bins,
                label="declared warm-up quarter", color="#e08a1e", alpha=0.7)
        ax.axvline(0.80, color="#444444", linestyle="--", linewidth=1)
        ax.text(0.80, ax.get_ylim()[1] * 0.95, " warning 0.80", fontsize=7.5, va="top")
        ax.axvline(1.00, color="#b3401f", linestyle="--", linewidth=1)
        ax.text(1.00, ax.get_ylim()[1] * 0.85, " hard limit 1.00", fontsize=7.5, va="top", ha="right")
        ax.legend(fontsize=8)
        return fig, ax

    def fig_queues():
        fig, ax = plt.subplots(figsize=(9, 5))
        q = out["routing_queue_reasons"].sort_values("decisions")
        ax.barh(q.queue_code + "  " + q.queue_name, q.decisions, color="#e08a1e")
        for i, v in enumerate(q.decisions):
            ax.text(v, i, f"  {v:,}", va="center", fontsize=8)
        ax.set_xlim(0, q.decisions.max() * 1.2)
        return fig, ax

    def fig_specialist_supply():
        fig, ax = plt.subplots(figsize=(9, 5))
        s = out["specialist_demand_by_industry"].sort_values("decisions")
        lab = s.specialist_requirement_level + " " + s.industry_code
        ax.barh(lab, s.decisions, color="#7f9aa8", label="decisions needing a specialist")
        ax.barh(lab, s.recommended_to_a_specialist, color="#1f5f8b", label="recommended to a specialist")
        ax.legend(fontsize=8)
        return fig, ax

    def fig_stability():
        fig, ax = plt.subplots(figsize=(9, 5))
        st = sens["recommendation_stability"]
        for pid, grp in st.groupby("perturbed"):
            g = grp.sort_values("weight_delta")
            ax.plot(g.weight_delta, g.recommendation_change_share, marker="o", markersize=3.5, label=pid, linewidth=1.2)
        ax.legend(fontsize=7, ncol=2)
        ax.axhline(0, color="#888888", linewidth=0.8)
        return fig, ax

    specs = [
        ("fig01_priority_band_distribution", fig_bands,
         f"Recommended priority bands before and after declared overrides ({PERIOD_SHORT})",
         "recommended priority band", "scored routing decisions", "routing decisions",
         "Which leads would be worked first, and how much of that comes from an override rather than the score?"),
        ("fig02_priority_component_contributions", fig_contrib,
         f"Mean contribution of each priority component, with its share of score variance ({PERIOD_SHORT})",
         "mean contribution to the 0-100 priority score", "component", "score points",
         "Which components actually move the lead score?"),
        ("fig03_routing_outcomes_by_decision_type", fig_outcomes,
         f"Routing outcome by decision type ({PERIOD_SHORT})", "decision type", "routing decisions", "routing decisions",
         "How often do the declared rules produce an RM rather than a controlled queue?"),
        ("fig04_territory_fit_recommended_vs_historical", fig_territory,
         f"Effective territory coverage: recommended against synthetic historical assignments ({PERIOD_SHORT})",
         "policy", "share of assignments with effective territory coverage", "share of decisions",
         "Does each policy assign leads to an RM that actually covers the territory?"),
        ("fig05_specialist_fit_recommended_vs_historical", fig_specialist,
         f"Specialist fulfilment, mandatory and preferred kept separate ({PERIOD_SHORT})",
         "specialist requirement level", "share of decisions met by a specialist", "share of decisions",
         "How does each policy meet mandatory and preferred specialist requirements?"),
        ("fig06_capacity_at_recommendation", fig_capacity,
         f"RM utilization at the moment of recommendation, warm-up shown separately ({PERIOD_SHORT})",
         "utilization ratio in the as-of capacity snapshot", "recommendations", "recommendations",
         "How loaded is the RM the framework recommends, and where do the declared thresholds sit?"),
        ("fig07_queue_reasons", fig_queues,
         f"Controlled queue outcomes by reason ({PERIOD_SHORT})", "routing decisions", "queue", "routing decisions",
         "When no RM is eligible, why not, and who owns the result?"),
        ("fig08_specialist_demand_versus_availability", fig_specialist_supply,
         f"Specialist demand against the share actually reaching a specialist, by industry ({PERIOD_SHORT})",
         "routing decisions", "requirement level and NAICS 2022 sector", "routing decisions",
         "Where is specialist demand unmet under the current staffing?"),
        ("fig09_recommendation_stability", fig_stability,
         f"Share of recommendations that change when one preference weight moves ({PERIOD_SHORT})",
         "change in the declared preference weight", "share of recommendations that change", "share of decisions",
         "How stable is the recommended RM under small changes to the declared preferences?"),
    ]
    rendered, register = {}, []
    for fid, fn, title, xl, yl, unit, question in specs:
        pngs = []
        for _ in range(2):
            fig, ax = fn()
            fig.suptitle(title, fontsize=9.5, wrap=True)
            ax.set_xlabel(xl)
            ax.set_ylabel(yl)
            fig.text(0.01, 0.005, NOTE, fontsize=6.5, ha="left", va="bottom")
            fig.tight_layout(rect=(0, 0.03, 1, 0.95))
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, metadata={"Software": None})
            plt.close(fig)
            pngs.append(buf.getvalue())
        rendered[fid] = pngs[0]
        register.append({"figure_id": fid, "filename": f"outputs/lead_routing/{fid}.png", "title": title,
                         "business_question": question, "x_label": xl, "y_label": yl, "unit": unit,
                         "reproducible_render": hashlib.sha256(pngs[0]).hexdigest() == hashlib.sha256(pngs[1]).hexdigest()})
    return rendered, pd.DataFrame(register)


def main():
    import os
    os.chdir(ROOT)
    d = load()
    out = analyse(d)
    sens = sensitivity(d)
    ex, br = worked_examples(d)
    fnd = findings(d, out, sens)
    figs, fig_reg = figures(d, out, sens)

    files = {
        "lead_priority_results.csv": out["lead_priority_results"],
        "priority_component_summary.csv": out["priority_component_summary"],
        "priority_band_profile.csv": out["priority_band_profile"],
        "priority_sensitivity.csv": sens["priority_sensitivity"],
        "routing_recommendations_static.csv": out["routing_recommendations"],
        "routing_recommendations_operational.csv": out["operational_recommendations"],
        "capacity_ledger_effect.csv": out["capacity_ledger_effect"],
        "routing_candidate_summary.csv": out["routing_candidate_summary"],
        "routing_candidate_pool.csv": out["routing_candidate_pool"],
        "routing_exceptions.csv": out["routing_exceptions"],
        "routing_queue_reasons.csv": out["routing_queue_reasons"],
        "policy_comparison.csv": out["policy_comparison"],
        "same_rm_rate_by_channel.csv": out["same_rm_rate_by_channel"],
        "specialist_demand_gap.csv": out["specialist_demand_gap"],
        "specialist_demand_by_industry.csv": out["specialist_demand_by_industry"],
        "capacity_profile_at_recommendation.csv": out["capacity_profile_at_recommendation"],
        "capacity_pile_up_diagnostic.csv": out["capacity_pile_up_diagnostic"],
        "capacity_pile_up_summary.csv": out["capacity_pile_up_summary"],
        "workload_distribution.csv": out["workload_distribution"],
        "recommendation_stability.csv": sens["recommendation_stability"],
        "worked_examples.csv": ex,
        "worked_example_components.csv": br,
        "lead_routing_findings.csv": fnd,
        "figure_register.csv": fig_reg.drop(columns="reproducible_render"),
    }

    print("CONTROLS")
    forbidden = {"contact_email_fictional", "contact_phone_fictional", "legal_name", "operating_name", "contact_role"}
    bad = {n: sorted(set(df.columns) & forbidden) for n, df in files.items() if set(df.columns) & forbidden}
    check("no fictional contact or name field is written out", not bad, bad or f"{len(files)} files")
    gt_like = {n: [c for c in df.columns if any(k in c for k in ("expected_", "truth", "latent_", "planned_path",
                                                                 "arrival_kind", "primary_scenario"))]
               for n, df in files.items()}
    gt_like = {n: v for n, v in gt_like.items() if v}
    check("no ground-truth field is written out", not gt_like, gt_like or "clean")
    check("all three priority bands appear in the band profile",
          set(out["priority_band_profile"].recommended_priority_band) == {"P1", "P2", "P3"},
          sorted(out["priority_band_profile"].recommended_priority_band))
    check("every finding carries a non-causal interpretation",
          set(fnd.interpretation) <= {"rule-adherence comparison", "routing-policy counterfactual",
                                      "operational exposure", "assignment-quality proxy"},
          sorted(set(fnd.interpretation)))
    causal = [t for t in fnd.finding.tolist() + fnd.evidence.tolist()
              if any(w in t.lower() for w in ("improves conversion", "increases revenue", "causes", "uplift",
                                              "drives conversion", "profitability gain"))]
    check("no finding claims a causal effect on conversion, revenue, profitability or experience", not causal, causal or "clean")
    check("every worked example was selected by a deterministic rule and resolves to a real decision",
          len(ex) >= 8 and ex.decision_id.notna().all(), f"{len(ex)} examples")
    check("figures render reproducibly", bool(fig_reg.reproducible_render.all()), f"{len(fig_reg)} figures")

    stamped = {n: stamp(df) for n, df in files.items()}
    for n, df in stamped.items():
        cols = set(df.columns)
        check(f"output carries source, period, unit and eligibility: {n}",
              {"source_models", "period", "statistical_unit", "evidence_eligibility", "limitations"} <= cols)

    if not all(c["passed"] for c in CHECKS):
        print("ANALYSIS FAILED: a control did not pass; nothing was published.")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    for n, df in stamped.items():
        df.to_csv(OUT / n, index=False, lineterminator="\n")
    for fid, png in figs.items():
        (FIG / f"{fid}.png").write_bytes(png)
    pd.DataFrame(CHECKS).to_csv(OUT / "analysis_controls.csv", index=False, lineterminator="\n")
    print(f"\nPublished {len(stamped)} analysis files to {OUT.relative_to(ROOT)} and "
          f"{len(figs)} figures to {FIG.relative_to(ROOT)}")
    print("LEAD PRIORITIZATION AND ROUTING ANALYSIS PASSED")


if __name__ == "__main__":
    main()
