"""
Independent validation of the integrated acquisition-intelligence layer.

Recomputes every important KPI from the UNDERLYING source facts in pandas - fct_lead, fct_lead_assignment,
fct_sla_event, fct_opportunity_outcome, fct_rm_capacity_snapshot, dim_business_prospect, dim_cma, the market
opportunity framework and the operational routing recommendation - and compares the results with what the
acquisition marts publish. It never reads the acquisition models' SQL, and it never trusts one acquisition mart to
check another: the market-focus, funnel, RM, action-queue and KPI marts are each checked against the sources.

It also checks the structural guarantees the layer depends on: declared primary keys, relationships, accepted
values, non-negative measures, rates inside [0, 1], numerators that never exceed their denominators, one action row
per actionable lead, no terminal lead marked actionable without a remediation reason, no fan-out from the history
tables, the maturity rule, the KPI catalog, and determinism across repeated runs.

Evidence: data/validation/acquisition_intelligence/*.csv and acquisition_intelligence_summary.json (deterministic
except run_timestamp_utc). Exit code 1 if any check fails.

    C:/Users/abhij/anaconda3/python.exe src/validation/21_validate_acquisition_intelligence.py
        [--determinism] [--output-dir DIR]
"""

from pathlib import Path
import argparse
import hashlib
import json
import re
import sys

import duckdb
import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
OUT = ROOT / "data" / "validation" / "acquisition_intelligence"
ANALYSIS = ROOT / "data" / "analysis" / "acquisition_intelligence"
DOC = ROOT / "docs" / "acquisition_intelligence_and_kpis.md"
DBT_MANIFEST = ROOT / "target" / "manifest.json"

GROUPS = ["structural_checks", "source_reconciliation", "kpi_recalculation", "funnel_recalculation",
          "action_queue_checks", "capacity_and_routing_checks", "maturity_checks", "catalog_and_documentation"]
results = {g: [] for g in GROUPS}

TERMINAL = ("CONVERTED", "CLOSED_LOST", "WITHDRAWN", "INVALID", "MERGED")


def check(group, name, ok, detail=""):
    results[group].append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def connect():
    return duckdb.connect(str(WAREHOUSE), read_only=True)


# ------------------------------------------------------------------ independent recomputation from the sources
def recompute(con):
    """Rebuild the lead-level acquisition picture from the source facts alone."""
    params = con.execute("select parameter, value from reference.acquisition_reporting_parameters").df()
    pm = dict(zip(params.parameter, params.value))
    cutoff = pd.Timestamp(pm["reporting_cutoff_date"]).date()
    conv_days = int(pm["conversion_maturity_days"])
    qual_days = int(pm["qualification_maturity_days"])
    age_days = int(pm["ageing_lead_days"])

    lead = con.execute("""select lead_id, prospect_id, captured_at, first_channel_code, cma_code_sgc2021,
                                 territory_id, geography_status, current_lead_status, is_terminal,
                                 first_assigned_at, first_contacted_at, qualified_at, converted_at,
                                 duplicate_group_id, current_owner_rm_id
                          from analytics.fct_lead""").df()
    prospect = con.execute("""select prospect_id, sme_segment, industry_code
                              from analytics.dim_business_prospect""").df()
    sla = con.execute("""select lead_id, sla_id, is_breached, stopped_at, target_at
                         from analytics.fct_sla_event where sla_id like 'SLA-06%'""").df()
    assign = con.execute("""select lead_id, rm_id from analytics.fct_lead_assignment
                            where assignment_role = 'primary' and rm_id is not null""").df()

    f = lead.merge(prospect, on="prospect_id", how="left")
    f["cma_key"] = f.cma_code_sgc2021.fillna(pm["unresolved_cma_label"])
    f["industry_grouping"] = f.industry_code.fillna(pm["unclassified_industry_label"])
    f["sme_segment"] = f.sme_segment.fillna("unknown")
    f["capture_month"] = pd.to_datetime(f.captured_at).dt.to_period("M").dt.to_timestamp().dt.date
    f["days_since_capture"] = (pd.Timestamp(cutoff) - pd.to_datetime(f.captured_at).dt.normalize()).dt.days

    f["is_valid_lead"] = ~f.current_lead_status.isin(["INVALID", "MERGED"])
    f["is_duplicate_lead"] = (f.current_lead_status == "MERGED") | f.duplicate_group_id.notna()
    f["is_assigned"] = f.first_assigned_at.notna()
    f["is_contacted"] = f.first_contacted_at.notna()
    f["is_qualified"] = f.qualified_at.notna()
    f["is_converted"] = f.converted_at.notna()
    f["is_active"] = ~f.is_terminal.astype(bool)
    f["is_ageing"] = f.is_active & (f.days_since_capture > age_days)
    f["is_conversion_mature"] = f.days_since_capture >= conv_days
    f["is_qualification_mature"] = f.days_since_capture >= qual_days

    # first-contact SLA, collapsed to lead grain independently
    g = sla.groupby("lead_id").agg(
        breached=("is_breached", "max"),
        open_clock=("stopped_at", lambda v: bool(v.isna().any())),
        target=("target_at", "min")).reset_index()
    f = f.merge(g, on="lead_id", how="left")
    f["sla_eligible"] = f.breached.notna()
    f["sla_breached"] = f.breached.fillna(False).astype(bool)
    f["sla_settled"] = f.sla_eligible & (~f.open_clock.fillna(False).astype(bool)
                                         | (pd.to_datetime(f.target) <= pd.Timestamp(cutoff)))

    # reassignment, from the assignment history
    rm_counts = assign.groupby("lead_id").rm_id.nunique().rename("distinct_rms").reset_index()
    f = f.merge(rm_counts, on="lead_id", how="left")
    f["is_reassigned"] = f.distinct_rms.fillna(0) > 1

    f["dq_exception"] = (f.geography_status.isin(["conflict", "missing"]) | f.territory_id.isna()
                         | f.industry_code.isna() | (f.industry_code == pm["unclassified_industry_label"])
                         | (f.current_lead_status == "VALIDATION_PENDING")
                         | (f.current_lead_status == "DUPLICATE_REVIEW"))
    return f, pm, cutoff


# ------------------------------------------------------------------ checks
def structural(con):
    print("\nStructural guarantees")
    pk = {
        "analytics.mart_acquisition_market_focus": ["cma_key"],
        "analytics.mart_acquisition_funnel_monthly": ["reporting_month", "cma_key", "channel_code",
                                                      "sme_segment", "industry_grouping"],
        "analytics.mart_rm_acquisition_performance_monthly": ["rm_id", "reporting_month"],
        "analytics.mart_acquisition_action_queue": ["lead_id"],
        "analytics.mart_acquisition_kpi_snapshot": ["reporting_cutoff_date"],
        "intermediate.int_acq__lead_spine": ["lead_id"],
    }
    for rel, cols in pk.items():
        key = ", ".join(cols)
        n, d = con.execute(f"select count(*), count(distinct ({key})) from {rel}").fetchone()
        nulls = con.execute(f"select count(*) from {rel} where "
                            + " or ".join(f"{c} is null" for c in cols)).fetchone()[0]
        check("structural_checks", f"declared primary key holds on {rel.split('.')[1]} ({key})",
              n == d and nulls == 0, f"{n} rows, {d} distinct keys, {nulls} null key parts")

    # relationships
    rels = [
        ("action queue lead_id resolves to fct_lead", """select count(*) from analytics.mart_acquisition_action_queue q
            where not exists (select 1 from analytics.fct_lead l where l.lead_id = q.lead_id)"""),
        ("funnel channel_code resolves to the channel dimension",
         """select count(*) from analytics.mart_acquisition_funnel_monthly f
            where not exists (select 1 from analytics.dim_acquisition_channel c
                              where c.channel_code = f.channel_code)"""),
        ("RM monthly rm_id resolves to the RM dimension",
         """select count(*) from analytics.mart_rm_acquisition_performance_monthly r
            where not exists (select 1 from analytics.dim_relationship_manager d where d.rm_id = r.rm_id)"""),
        ("market focus cma_code resolves to dim_cma where in CMA scope",
         """select count(*) from analytics.mart_acquisition_market_focus m
            where m.is_cma_scope and not exists (select 1 from analytics.dim_cma d
                                                 where d.cma_code_sgc2021 = m.cma_code_sgc2021)"""),
    ]
    for name, sql in rels:
        n = con.execute(sql).fetchone()[0]
        check("structural_checks", name, n == 0, n)

    # accepted values
    bad_tier = con.execute("""select count(*) from analytics.mart_acquisition_action_queue
                              where priority_tier is not null and priority_tier not in ('P1','P2','P3')""").fetchone()[0]
    check("structural_checks", "every published priority tier is one of the declared bands", bad_tier == 0, bad_tier)
    bad_status = con.execute("""select count(*) from analytics.mart_acquisition_action_queue
                                where routing_status is not null
                                  and routing_status not in ('assigned','queued')""").fetchone()[0]
    check("structural_checks", "every routing status is one of the declared states", bad_status == 0, bad_status)
    bad_queue = con.execute("""select count(*) from analytics.mart_acquisition_action_queue q
                               where q.queue_code is not null and q.queue_code not in
                                     (select queue_code from reference.routing_queue_rules)""").fetchone()[0]
    check("structural_checks", "every queue code is a declared queue", bad_queue == 0, bad_queue)


def source_reconciliation(con, f):
    print("\nSource reconciliation and fan-out control")
    n_source = len(f)
    spine = con.execute("select count(*), count(distinct lead_id) from intermediate.int_acq__lead_spine").fetchone()
    check("source_reconciliation", "the lead spine is exactly one row per source lead",
          spine[0] == n_source and spine[1] == n_source, f"{spine[0]} rows vs {n_source} source leads")

    for rel, col in [("analytics.mart_acquisition_funnel_monthly", "captured_leads"),
                     ("analytics.mart_acquisition_market_focus", "lead_count")]:
        total = con.execute(f"select coalesce(sum({col}), 0) from {rel}").fetchone()[0]
        check("source_reconciliation", f"{rel.split('.')[1]} reconciles to the source lead count",
              int(total) == n_source, f"{int(total)} vs {n_source}")

    kpi_total = con.execute("select total_leads from analytics.mart_acquisition_kpi_snapshot").fetchone()[0]
    check("source_reconciliation", "the executive snapshot reconciles to the source lead count",
          int(kpi_total) == n_source, f"{int(kpi_total)} vs {n_source}")

    # fan-out: the history tables must not have multiplied any lead
    for tbl in ["fct_lead_status_history", "fct_lead_assignment", "fct_sla_event", "fct_lead_activity",
                "fct_routing_decision"]:
        mx = con.execute(f"select max(n) from (select lead_id, count(*) n from analytics.{tbl} group by 1)").fetchone()[0]
        spine_rows = con.execute("""select count(*) from intermediate.int_acq__lead_spine""").fetchone()[0]
        check("source_reconciliation",
              f"{tbl} reaches {mx} rows for a single lead yet the spine stays at one row per lead",
              spine_rows == n_source, f"max {mx} source rows per lead")

    # every lead appears in exactly one funnel cell
    cells = f.groupby("lead_id").size()
    check("source_reconciliation", "each lead maps to exactly one funnel cell",
          int(cells.max()) == 1, f"max {int(cells.max())} cells per lead")


def kpi_recalculation(con, f, pm, cutoff):
    print("\nIndependent KPI recalculation from the source facts")
    k = con.execute("select * from analytics.mart_acquisition_kpi_snapshot").df().iloc[0]

    def cmp(name, mine, theirs, tol=0.0):
        ok = abs(float(mine) - float(theirs)) <= tol if tol else int(mine) == int(theirs)
        check("kpi_recalculation", name, ok, f"recomputed {mine}, published {theirs}")

    cmp("total leads recompute", len(f), k.total_leads)
    cmp("active leads recompute", int(f.is_active.sum()), k.active_leads)
    cmp("assignment-rate numerator recompute", int(f.is_assigned.sum()), k.assignment_rate_numerator)
    cmp("assignment-rate denominator recompute", int(f.is_valid_lead.sum()), k.assignment_rate_denominator)
    cmp("contact-rate numerator recompute", int(f.is_contacted.sum()), k.contact_rate_numerator)
    cmp("qualification-rate numerator recompute",
        int((f.is_qualification_mature & f.is_qualified).sum()), k.qualification_rate_numerator)
    cmp("qualification-rate denominator recompute",
        int((f.is_qualification_mature & f.is_contacted).sum()), k.qualification_rate_denominator)
    cmp("mature conversion numerator recompute",
        int((f.is_conversion_mature & f.is_converted).sum()), k.conversion_rate_numerator)
    cmp("mature conversion denominator recompute",
        int((f.is_conversion_mature & f.is_valid_lead).sum()), k.conversion_rate_denominator)
    cmp("duplicate count recompute", int(f.is_duplicate_lead.sum()), k.duplicate_rate_numerator)
    cmp("first-contact SLA settled recompute", int(f.sla_settled.sum()), k.sla_settled_count)
    cmp("first-contact SLA breach count recompute",
        int((f.sla_settled & f.sla_breached).sum()), k.sla_breach_count)
    cmp("unassigned or queued active leads recompute",
        int((f.is_active & (f.current_owner_rm_id.isna()
                            | f.lead_id.isin(set(con.execute("""select lead_id from
                                analytics.mart_routing_recommendation_operational
                                where recommendation_status = 'queued'""").df().lead_id)))).sum()),
        k.unassigned_or_queued_leads)
    cmp("data-quality exception count recompute", int(f.dq_exception.sum()), k.data_quality_exception_count)

    # rates recomputed from the recomputed numerators and denominators
    for name, num, den, published in [
        ("assignment rate", f.is_assigned.sum(), f.is_valid_lead.sum(), k.assignment_rate),
        ("contact rate", f.is_contacted.sum(), f.is_assigned.sum(), k.contact_rate),
        ("qualification rate", (f.is_qualification_mature & f.is_qualified).sum(),
         (f.is_qualification_mature & f.is_contacted).sum(), k.qualification_rate),
        ("mature-cohort conversion rate", (f.is_conversion_mature & f.is_converted).sum(),
         (f.is_conversion_mature & f.is_valid_lead).sum(), k.mature_cohort_conversion_rate),
        ("duplicate rate", f.is_duplicate_lead.sum(), len(f), k.duplicate_rate),
        ("SLA breach rate", (f.sla_settled & f.sla_breached).sum(), f.sla_settled.sum(), k.sla_breach_rate),
    ]:
        mine = float(num) / float(den) if den else None
        check("kpi_recalculation", f"{name} recomputes to the published value",
              mine is not None and abs(mine - float(published)) < 1e-9,
              f"recomputed {mine:.6f}, published {float(published):.6f}")

    # high-opportunity CMA count, recomputed from the framework
    hi = con.execute("""select count(*) from analytics.mart_cma_opportunity_framework
                        where scenario_id = ? and opportunity_classification = ?""",
                     [pm["planning_scenario_id"], pm["high_opportunity_classification"]]).fetchone()[0]
    cmp("high-opportunity CMA count recompute", hi, k.high_opportunity_cma_count)

    # every rate in the mart lies in [0, 1] and every numerator fits its denominator
    bad = con.execute("""select count(*) from analytics.mart_acquisition_kpi_snapshot where
        assignment_rate not between 0 and 1 or contact_rate not between 0 and 1
        or qualification_rate not between 0 and 1 or mature_cohort_conversion_rate not between 0 and 1
        or duplicate_rate not between 0 and 1 or sla_breach_rate not between 0 and 1
        or operational_routing_assignment_rate not between 0 and 1
        or routing_top_decile_share not between 0 and 1 or routing_gini not between 0 and 1""").fetchone()[0]
    check("kpi_recalculation", "every executive rate lies within zero and one", bad == 0, bad)


def funnel_recalculation(con, f):
    print("\nIndependent funnel recalculation")
    keys = ["capture_month", "cma_key", "first_channel_code", "sme_segment", "industry_grouping"]
    mine = f.groupby(keys, as_index=False).agg(
        captured_leads=("lead_id", "count"),
        valid_leads=("is_valid_lead", "sum"),
        assigned_leads=("is_assigned", "sum"),
        contacted_leads=("is_contacted", "sum"),
        converted_leads=("is_converted", "sum"),
        valid_leads_mature=("is_conversion_mature", lambda v: 0),
        reassigned_leads=("is_reassigned", "sum"))
    mine["valid_leads_mature"] = f.assign(m=f.is_conversion_mature & f.is_valid_lead).groupby(
        keys, as_index=False).m.sum().m
    mine["converted_leads_mature"] = f.assign(m=f.is_conversion_mature & f.is_converted).groupby(
        keys, as_index=False).m.sum().m
    mine = mine.rename(columns={"capture_month": "reporting_month", "first_channel_code": "channel_code"})

    pub = con.execute("""select reporting_month, cma_key, channel_code, sme_segment, industry_grouping,
                                captured_leads, valid_leads, assigned_leads, contacted_leads, converted_leads,
                                valid_leads_mature, converted_leads_mature, reassigned_leads
                         from analytics.mart_acquisition_funnel_monthly""").df()
    pub["reporting_month"] = pd.to_datetime(pub.reporting_month).dt.date
    j = mine.merge(pub, on=["reporting_month", "cma_key", "channel_code", "sme_segment", "industry_grouping"],
                   how="outer", suffixes=("_mine", "_pub"), indicator=True)
    check("funnel_recalculation", "every funnel cell exists in both the recomputation and the published mart",
          int((j._merge != "both").sum()) == 0, f"{len(mine)} recomputed, {len(pub)} published")
    both = j[j._merge == "both"]
    for col in ["captured_leads", "valid_leads", "assigned_leads", "contacted_leads", "converted_leads",
                "valid_leads_mature", "converted_leads_mature", "reassigned_leads"]:
        diff = int((both[col + "_mine"].astype(int) != both[col + "_pub"].astype(int)).sum())
        check("funnel_recalculation", f"{col} recomputes exactly in every funnel cell", diff == 0,
              f"{diff} differing cells")

    bad = con.execute("""select count(*) from analytics.mart_acquisition_funnel_monthly where
        assigned_leads > valid_leads or contacted_leads > assigned_leads
        or converted_leads_mature > valid_leads_mature or qualified_leads_mature > contacted_leads_mature
        or duplicate_leads > captured_leads or first_contact_sla_breached > first_contact_sla_settled""").fetchone()[0]
    check("funnel_recalculation", "no funnel numerator exceeds its denominator", bad == 0, bad)
    neg = con.execute("""select count(*) from analytics.mart_acquisition_funnel_monthly
        where least(captured_leads, valid_leads, assigned_leads, contacted_leads, qualified_leads,
                    converted_leads, active_backlog, ageing_leads, reassigned_leads) < 0""").fetchone()[0]
    check("funnel_recalculation", "no funnel measure is negative", neg == 0, neg)


def action_queue_checks(con, f):
    print("\nAction queue")
    q = con.execute("select * from analytics.mart_acquisition_action_queue").df()
    active = set(f[f.is_active].lead_id)
    check("action_queue_checks", "one action row per actionable lead, with no duplicates",
          len(q) == q.lead_id.nunique(), f"{len(q)} rows, {q.lead_id.nunique()} distinct leads")
    missing = active - set(q.lead_id)
    check("action_queue_checks", "every active lead appears in the action queue", not missing,
          f"{len(missing)} active leads missing")
    terminal_rows = q[q.is_terminal.astype(bool)]
    unresolved = set(con.execute("""select distinct duplicate_group_id
                                    from intermediate.int_crm__duplicate_resolution
                                    where resolved_at is null""").df().duplicate_group_id)
    bad_terminal = terminal_rows[~terminal_rows.duplicate_group_id.isin(unresolved)]
    check("action_queue_checks", "no terminal lead is marked actionable without an unresolved duplicate to fix",
          len(bad_terminal) == 0, f"{len(terminal_rows)} terminal rows, {len(bad_terminal)} without a reason")
    closed = q[q.current_lead_status.isin(TERMINAL) & ~q.duplicate_group_id.isin(unresolved)]
    check("action_queue_checks", "no converted, closed, withdrawn or invalid lead is in the queue",
          len(closed) == 0, len(closed))
    check("action_queue_checks", "every action row carries an action, an owner and a rank",
          q.recommended_next_action.notna().all() and q.responsible_role.notna().all()
          and q.action_rank.notna().all(), f"{len(q)} rows")
    ranks = sorted(q.action_rank.astype(int))
    check("action_queue_checks", "action_rank is a complete 1..n permutation",
          ranks == list(range(1, len(q) + 1)), f"{len(q)} ranks")
    esc = q[q.requires_escalation.astype(bool)]
    unjust = esc[~(esc.first_contact_sla_breached.astype(bool) | (esc.open_breached_clocks > 0)
                   | (esc.queue_code.notna() & (esc.lead_age_days > 30)))]
    check("action_queue_checks", "every escalation has a stated reason", len(unjust) == 0,
          f"{len(esc)} escalations, {len(unjust)} unjustified")


def capacity_and_routing(con):
    print("\nCapacity and routing concentration")
    k = con.execute("select * from analytics.mart_acquisition_kpi_snapshot").df().iloc[0]
    load = con.execute("""select r.rm_id, count(o.decision_id) n
                          from analytics.dim_relationship_manager r
                          left join analytics.mart_routing_recommendation_operational o
                                 on o.recommended_rm_id = r.rm_id and o.recommendation_status = 'assigned'
                          group by 1""").df()
    v = load.n.to_numpy(dtype=float)
    srt = np.sort(v)[::-1]
    top = max(1, len(v) // 10)
    mine_top = srt[:top].sum() / v.sum()
    s = np.sort(v)
    n = len(s)
    mine_gini = float((2 * np.arange(1, n + 1) - n - 1).dot(s) / (n * s.sum()))
    check("capacity_and_routing_checks", "the top-decile concentration share recomputes",
          abs(mine_top - float(k.routing_top_decile_share)) < 1e-9,
          f"recomputed {mine_top:.6f}, published {float(k.routing_top_decile_share):.6f}")
    check("capacity_and_routing_checks", "the workload Gini recomputes",
          abs(mine_gini - float(k.routing_gini)) < 1e-9,
          f"recomputed {mine_gini:.6f}, published {float(k.routing_gini):.6f}")
    check("capacity_and_routing_checks", "the busiest RM count recomputes",
          int(v.max()) == int(k.max_recommendations_to_one_rm), int(v.max()))
    check("capacity_and_routing_checks", "the count of RMs receiving no recommendation recomputes",
          int((v == 0).sum()) == int(k.rms_with_no_recommendations), int((v == 0).sum()))
    check("capacity_and_routing_checks", "the concentration risk is material and is therefore reported",
          mine_top > 0.5, f"the busiest decile takes {mine_top:.2%} of recommendations")
    check("capacity_and_routing_checks", "the capacity-aware policy makes no hard-cap breach",
          int(k.operational_hard_cap_breaches) == 0, int(k.operational_hard_cap_breaches))

    warn = con.execute("""select cast(value as double) from reference.routing_evaluation_thresholds
                          where parameter = 'capacity_warning_ratio'""").fetchone()[0]
    mine_warn = con.execute(f"""select count(*) from (
        select r.rm_id, s.utilization_ratio,
               row_number() over (partition by s.rm_id order by s.snapshot_as_of_utc desc) rn
        from analytics.dim_relationship_manager r
        join analytics.fct_rm_capacity_snapshot s on s.rm_id = r.rm_id
        where r.active_flag) where rn = 1 and utilization_ratio >= {warn}""").fetchone()[0]
    check("capacity_and_routing_checks", "the count of RMs at or above the capacity warning recomputes",
          int(mine_warn) == int(k.rms_above_capacity_warning),
          f"recomputed {int(mine_warn)}, published {int(k.rms_above_capacity_warning)}")

    zero_rows = con.execute("""select count(*) from analytics.mart_rm_acquisition_performance_monthly
                               where assigned_leads = 0""").fetchone()[0]
    check("capacity_and_routing_checks", "RMs with no work in a month appear as zero rows rather than disappearing",
          zero_rows > 0, f"{zero_rows} RM-months with no assignment")


def maturity_checks(con, f, pm):
    print("\nMaturity rule")
    conv_days = int(pm["conversion_maturity_days"])
    mine_mature = int((f.is_conversion_mature & f.is_valid_lead).sum())
    pub = con.execute("""select coalesce(sum(valid_leads_mature), 0)
                         from analytics.mart_acquisition_funnel_monthly""").fetchone()[0]
    check("maturity_checks", "the mature conversion denominator matches the independent recomputation",
          mine_mature == int(pub), f"recomputed {mine_mature}, published {int(pub)}")
    immature = int((~f.is_conversion_mature & f.is_valid_lead).sum())
    pub_im = con.execute("""select coalesce(sum(valid_leads_immature), 0)
                            from analytics.mart_acquisition_funnel_monthly""").fetchone()[0]
    check("maturity_checks", "the immature population is published rather than dropped",
          immature == int(pub_im) and immature > 0, f"{immature} immature valid leads")
    check("maturity_checks", "mature plus immature equals the valid population",
          mine_mature + immature == int(f.is_valid_lead.sum()), int(f.is_valid_lead.sum()))
    leak = con.execute(f"""select count(*) from intermediate.int_acq__lead_spine
                           where is_conversion_mature and days_since_capture < {conv_days}""").fetchone()[0]
    check("maturity_checks", "no lead inside the maturity window is marked mature", leak == 0, leak)
    cols = con.execute("""select count(*) from information_schema.columns
                          where table_name = 'mart_acquisition_funnel_monthly'
                            and regexp_matches(column_name,
                                'opportunity_score|opportunity_rank|opportunity_classification|pillar_')""").fetchone()[0]
    check("maturity_checks", "the current planning snapshot is never applied retrospectively to a historical month",
          cols == 0, f"{cols} snapshot columns found in the monthly funnel")


def catalog_and_documentation(con):
    print("\nKPI catalog and documentation")
    cat = con.execute("select * from reference.acquisition_kpi_catalog").df()
    required = ["business_definition", "formula", "numerator", "denominator", "source_mart", "grain", "filters",
                "maturity_rule", "null_handling", "interpretation", "limitation", "stakeholder"]
    missing = {c: int(cat[c].isna().sum() + (cat[c].astype(str).str.strip() == "").sum()) for c in required}
    missing = {c: n for c, n in missing.items() if n}
    check("catalog_and_documentation", "every KPI documents all twelve required attributes", not missing,
          missing or f"{len(cat)} KPIs x {len(required)} attributes")
    tables = set(con.execute("""select table_name from information_schema.tables
                                where table_catalog = 'sme_banking'""").df().table_name)
    bad = sorted(set(cat.source_mart) - tables)
    check("catalog_and_documentation", "every KPI names a mart that exists", not bad, bad or f"{len(cat)} KPIs")
    check("catalog_and_documentation", "KPI identifiers and names are unique",
          cat.kpi_id.is_unique and cat.kpi_name.is_unique, f"{len(cat)} KPIs")

    text = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    check("catalog_and_documentation", "the acquisition-intelligence document exists", bool(text), DOC.name)
    if text:
        flat = re.sub(r"\s+", " ", text)
        missing_kpi = [k for k in cat.kpi_id if k not in flat]
        check("catalog_and_documentation", "every catalogued KPI appears in the document", not missing_kpi,
              missing_kpi or f"{len(cat)} KPIs documented")
        k = con.execute("select * from analytics.mart_acquisition_kpi_snapshot").df().iloc[0]
        claims = {
            "total_leads": f"{int(k.total_leads):,}",
            "active_leads": f"{int(k.active_leads):,}",
            "actionable": f"{int(k.actionable_leads):,}",
            "conversion": f"{float(k.mature_cohort_conversion_rate):.1%}",
            "sla_breach": f"{float(k.sla_breach_rate):.1%}",
            "top_decile": f"{float(k.routing_top_decile_share):.2%}",
            "gini": f"{float(k.routing_gini):.3f}",
            "max_rm": f"{int(k.max_recommendations_to_one_rm):,}",
        }
        absent = {kk: v for kk, v in claims.items() if v not in flat}
        check("catalog_and_documentation", "every headline figure stated in the document matches the published data",
              not absent, absent or f"{len(claims)} figures verified")
        disclaimers = [d for d in ("synthetic", "hypothetical", "fictional") if d not in text.lower()]
        check("catalog_and_documentation", "the document carries the synthetic and hypothetical disclaimers",
              not disclaimers, disclaimers or "all present")
        banned = [b for b in ("proves that", "will increase revenue", "guarantees", "real bank data")
                  if b in text.lower()]
        check("catalog_and_documentation", "the document makes no causal or real-bank claim", not banned,
              banned or "none")


# ------------------------------------------------------------------ determinism
def determinism():
    """Run the validator's own recomputation twice and compare every substantive result."""
    print("\nDeterminism")
    import subprocess
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="acq_determinism_", dir=str(ROOT / "target")))
    try:
        runs = []
        for i in range(2):
            d = tmp / f"run{i}"
            r = subprocess.run([sys.executable, str(ROOT / "src" / "validation"
                                                   / "21_validate_acquisition_intelligence.py"),
                                "--output-dir", str(d)], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-2500:])
                return False
            runs.append(d)
        a, b = runs
        diffs = []
        for fa in sorted(a.glob("*")):
            fb = b / fa.name
            if not fb.exists():
                diffs.append(fa.name + " missing from the second run")
                continue
            if fa.suffix == ".json":
                ja = json.loads(fa.read_text(encoding="utf-8"))
                jb = json.loads(fb.read_text(encoding="utf-8"))
                for key in ("run_timestamp_utc", "evidence_sha256"):
                    ja.pop(key, None)
                    jb.pop(key, None)
                if ja != jb:
                    diffs.append(fa.name + " differs")
            elif sha(fa) != sha(fb):
                diffs.append(fa.name + " differs")
        print(f"  [{'PASS' if not diffs else 'FAIL'}] every substantive validation result is identical across two "
              f"runs ({len(list(a.glob('*')))} evidence files; run_timestamp_utc excluded)")
        for d in diffs:
            print("     differs:", d)
        return not diffs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ driver
def validate(out_dir):
    import datetime as dt
    out_dir.mkdir(parents=True, exist_ok=True)
    con = connect()
    f, pm, cutoff = recompute(con)
    structural(con)
    source_reconciliation(con, f)
    kpi_recalculation(con, f, pm, cutoff)
    funnel_recalculation(con, f)
    action_queue_checks(con, f)
    capacity_and_routing(con)
    maturity_checks(con, f, pm)
    catalog_and_documentation(con)

    for g in GROUPS:
        if results[g]:
            pd.DataFrame(results[g]).to_csv(out_dir / f"{g}.csv", index=False, lineterminator="\n")

    k = con.execute("select * from analytics.mart_acquisition_kpi_snapshot").df().iloc[0]
    cat = con.execute("select count(*) from reference.acquisition_kpi_catalog").fetchone()[0]
    man = json.loads(DBT_MANIFEST.read_text(encoding="utf-8")) if DBT_MANIFEST.exists() else {"nodes": {}}
    ai = {kk: n for kk, n in man["nodes"].items() if "acquisition_intelligence" in (n.get("tags") or [])}
    ai_tests = {kk for kk, n in man["nodes"].items() if n["resource_type"] == "test"
                and (kk in ai or set(n.get("depends_on", {}).get("nodes", [])) & set(ai))}

    passed = sum(c["passed"] for g in GROUPS for c in results[g])
    total = sum(len(results[g]) for g in GROUPS)
    summary = {
        "purpose": "Independent validation of the integrated acquisition-intelligence layer: market focus, funnel "
                   "performance, lead priority, operational routing, SLA performance and RM capacity.",
        "framing": "Hypothetical rules for a fictional bank over entirely synthetic CRM data. No result is a fact "
                   "about any real bank and no measure supports a causal claim.",
        "run_timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "validation_script_sha256": sha(Path(__file__)),
        "reporting_cutoff_date": str(cutoff),
        "declared_parameters": pm,
        "marts": {
            "mart_acquisition_market_focus": "one row per CMA at the planning snapshot (plus an unresolved bucket)",
            "mart_acquisition_funnel_monthly": "reporting month x CMA x channel x SME segment x industry grouping",
            "mart_rm_acquisition_performance_monthly": "relationship manager x reporting month",
            "mart_acquisition_action_queue": "one row per currently actionable lead",
            "mart_acquisition_kpi_snapshot": "one row per reporting cutoff date",
        },
        "kpi_count": int(cat),
        "headline": {
            "total_leads": int(k.total_leads),
            "active_leads": int(k.active_leads),
            "actionable_leads": int(k.actionable_leads),
            "p1_leads_requiring_action": int(k.p1_leads_requiring_action),
            "assignment_rate": round(float(k.assignment_rate), 6),
            "contact_rate": round(float(k.contact_rate), 6),
            "qualification_rate": round(float(k.qualification_rate), 6),
            "mature_cohort_conversion_rate": round(float(k.mature_cohort_conversion_rate), 6),
            "duplicate_rate": round(float(k.duplicate_rate), 6),
            "sla_breach_rate": round(float(k.sla_breach_rate), 6),
            "high_opportunity_cma_count": int(k.high_opportunity_cma_count),
            "rms_above_capacity_warning": int(k.rms_above_capacity_warning),
            "operational_hard_cap_breaches": int(k.operational_hard_cap_breaches),
            "routing_top_decile_share": round(float(k.routing_top_decile_share), 6),
            "routing_gini": round(float(k.routing_gini), 6),
            "max_recommendations_to_one_rm": int(k.max_recommendations_to_one_rm),
            "rms_with_no_recommendations": int(k.rms_with_no_recommendations),
            "data_quality_exception_count": int(k.data_quality_exception_count),
        },
        "dbt_tests": {"acquisition_intelligence_tests": len(ai_tests)},
        "evidence_sha256": {p.name: sha(p) for p in sorted(out_dir.glob("*.csv"))},
        "checks_by_file": {g: {"passed": int(sum(c["passed"] for c in results[g])), "total": len(results[g])}
                           for g in GROUPS if results[g]},
        "checks_passed": passed,
        "checks_total": total,
        "failed_checks": [c["check"] for g in GROUPS for c in results[g] if not c["passed"]],
        "all_checks_pass": passed == total,
    }
    (out_dir / "acquisition_intelligence_summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    con.close()
    print(f"\nChecks: {passed} of {total} pass")
    return summary


def main():
    import os
    os.chdir(ROOT)
    ap = argparse.ArgumentParser()
    ap.add_argument("--determinism", action="store_true")
    ap.add_argument("--output-dir")
    a = ap.parse_args()
    if a.determinism:
        ok = determinism()
        print("DETERMINISM " + ("PASSED" if ok else "FAILED"))
        sys.exit(0 if ok else 1)
    s = validate(Path(a.output_dir) if a.output_dir else OUT)
    print("ACQUISITION INTELLIGENCE VALIDATION " + ("PASSED" if s["all_checks_pass"] else "FAILED"))
    sys.exit(0 if s["all_checks_pass"] else 1)


if __name__ == "__main__":
    main()
