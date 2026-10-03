"""
Independent validation of the synthetic CRM data (fictional bank, fictional businesses and staff).

Reads the published Parquet entities (data/synthetic/crm), the separate generator ground truth
(data/synthetic/crm_ground_truth) and the design catalogs (data/design/crm), and recomputes everything it checks
without importing the generator:
- inventory: manifest complete, SHA-256 hashes, row counts inside the volume specification;
- schema: columns, order and types exactly as crm_field_dictionary.csv; required fields; accepted values;
  unique identifiers and their fictional patterns;
- referential integrity, including CMA, industry and size-band keys in the analytical warehouse;
- temporal consistency, SLA targets and outcomes recomputed on an independent business calendar, RM
  utilization recomputed from assignments, lead statuses and activities;
- lead lifecycle (allowed transitions, terminal behaviour, dispositions, audited reopening), assignment history,
  one active primary assignment, overrides, duplicates and source attribution;
- scenario coverage from observable predicates (then compared with the ground-truth labels);
- synthetic rates against the volume specification; geography, size, industry and channel coverage;
- privacy and safety: fictional identifiers, reserved email domains and 555-01xx numbers, no real bank names,
  no ground-truth or score fields in operational data;
- (full mode) dbt source reconciliation, staged models equal to their sources, foundational model grains, CRM dbt
  tests passing, upstream design catalogs unchanged.

Evidence: data/validation/synthetic_crm/*.csv and synthetic_crm_summary.json (deterministic except
run_timestamp_utc). Exit code 1 if any check fails.

    C:/Users/abhij/anaconda3/python.exe src/validation/17_validate_synthetic_crm_data.py [--source-only] [--data-root DIR]
        [--output-dir DIR] [--mutation-tests]
"""

from pathlib import Path
from bisect import bisect_left, bisect_right
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zoneinfo

import duckdb
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "design" / "crm"
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
DEFAULT_DATA = ROOT / "data" / "synthetic"
DEFAULT_OUT = ROOT / "data" / "validation" / "synthetic_crm"
DESIGN_SUMMARY = ROOT / "data" / "validation" / "crm_design" / "crm_design_summary.json"
RUN_RESULTS, DBT_MANIFEST = ROOT / "target" / "run_results.json", ROOT / "target" / "manifest.json"
UTC = dt.timezone.utc
GROUND_TRUTH_COLUMNS = {"primary_scenario_id", "scenario_id", "synthetic_scenario_id", "arrival_kind", "matched_lead_id_truth", "latent_engagement",
                        "planned_path", "first_contact_breach_planned", "injected_process_issues", "expected_final_status", "expected_conversion",
                        "expected_specialist_need", "expected_duplicate_outcome", "excluded_hard_limit_rm_ids", "boundary_exclusion",
                        "preferred_rm_ignored", "relationship", "related_prospect_id", "rule_expected", "reopened"}
SCORE_RE = re.compile(r"priority_score|routing_score|lead_score|recommended_rm|rm_recommendation|assignment_optimi|propensity")
REAL_BANKS = ["Royal Bank", "RBC", "Toronto-Dominion", "TD Bank", "TD Canada", "Bank of Montreal", "BMO", "Scotiabank", "Bank of Nova Scotia",
              "CIBC", "Canadian Imperial", "National Bank", "Desjardins", "HSBC", "Laurentian", "ATB Financial", "EQ Bank", "Tangerine",
              "Simplii", "Vancity", "Servus", "Meridian", "Coast Capital", "Manulife Bank", "Canadian Western Bank"]
VOLUME_ITEM = {"crm_business_prospect": "business prospects", "crm_lead": "leads", "crm_relationship_manager": "relationship managers",
               "crm_territory": "territories", "crm_lead_assignment": "assignments", "crm_lead_activity": "activities",
               "crm_rm_capacity_snapshot": "capacity snapshots", "crm_routing_decision": "routing decisions", "crm_sla_event": "SLA events",
               "crm_campaign": "campaigns"}
ID_PATTERN = {("crm_business_prospect", "prospect_id"): r"PRS-\d{6}", ("crm_business_prospect", "synthetic_business_id"): r"SYN-BIZ-\d{6}",
              ("crm_lead", "lead_id"): r"LED-\d{7}", ("crm_lead_source_touch", "source_touch_id"): r"TCH-\d{7}",
              ("crm_campaign", "campaign_id"): r"CMP-\d{4}", ("crm_relationship_manager", "rm_id"): r"RM-\d{4}",
              ("crm_relationship_manager", "rm_code"): r"RM-FICT-\d{4}", ("crm_rm_territory", "rm_territory_id"): r"RMT-\d{6}",
              ("crm_rm_specialization", "rm_specialization_id"): r"RMS-\d{6}", ("crm_rm_capacity_snapshot", "capacity_snapshot_id"): r"CAPS-\d{7}",
              ("crm_routing_decision", "routing_decision_id"): r"RTD-\d{7}", ("crm_lead_assignment", "assignment_id"): r"ASG-\d{7}",
              ("crm_lead_activity", "activity_id"): r"ACT-\d{7}", ("crm_lead_status_history", "status_history_id"): r"LSH-\d{7}",
              ("crm_sla_event", "sla_event_id"): r"SLE-\d{7}", ("crm_duplicate_group", "duplicate_group_id"): r"DUPG-\d{6}",
              ("crm_opportunity_outcome", "opportunity_id"): r"OPP-\d{7}", ("crm_rule_version", "rule_version_id"): r"RV-[A-Z]+-\d{3}",
              ("crm_territory", "territory_id"): r"TER-(CMA-\d{3}|PRV-\d{2}|REMOTE-NORTH|NATIONAL-SPECIALIST)"}
SCENARIO_EXACT = ["SC-06", "SC-07", "SC-08", "SC-09", "SC-12", "SC-16", "SC-18", "SC-21", "SC-22", "SC-24", "SC-25", "SC-26", "SC-33"]
results = {k: [] for k in ["entity_inventory", "schema_validation", "referential_integrity_checks", "temporal_consistency_checks",
                           "status_transition_checks", "scenario_coverage", "distribution_validation", "privacy_and_safety_checks",
                           "dbt_source_reconciliation", "model_grain_checks"]}


def check(group, name, ok, detail=""):
    results[group].append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def design(name):
    return pd.read_csv(DESIGN / name, dtype=str, keep_default_na=False)


# ------------------------------------------------------------------ independent business calendar
class BusinessCalendar:
    """Business seconds between instants for a province (08:30-17:00 local, weekdays, holidays), from business_calendar.csv."""

    def __init__(self, cal):
        st = {r.description: r.value for r in cal.itertuples() if r.entry_type == "setting"}
        h0, m0 = (int(x) for x in st["business_day_start_local"].split(":"))
        h1, m1 = (int(x) for x in st["business_day_end_local"].split(":"))
        hol = {}
        for r in cal.itertuples():
            if r.entry_type == "holiday":
                for p in r.province_codes.split(";"):
                    hol.setdefault(p, set()).add(dt.date.fromisoformat(r.calendar_date))
        self.tables = {}
        zones = {r.province_codes: r.value for r in cal.itertuples() if r.entry_type == "setting" and r.description.startswith("zone_") and r.province_codes}
        zones[None] = st["zone_default"]
        days = [dt.date(2025, 5, 15) + dt.timedelta(days=i) for i in range((dt.date(2026, 9, 30) - dt.date(2025, 5, 15)).days + 1)]
        for p, zname in zones.items():
            z = zoneinfo.ZoneInfo(zname)
            mids, starts, lens, cum, total = [], [], [], [], 0
            for d in days:
                open_ = d.weekday() < 5 and d not in hol.get("ALL", set()) and (p is None or d not in hol.get(p, set()))
                a = int(dt.datetime(d.year, d.month, d.day, h0, m0, tzinfo=z).timestamp())
                b = int(dt.datetime(d.year, d.month, d.day, h1, m1, tzinfo=z).timestamp())
                mids.append(int(dt.datetime(d.year, d.month, d.day, tzinfo=z).timestamp()))
                starts.append(a)
                lens.append(b - a if open_ else 0)
                cum.append(total)
                total += lens[-1]
            self.tables[p] = (mids, starts, lens, cum, [c + ln for c, ln in zip(cum, lens)])
        self.zone_of = zones

    def position(self, p, t):
        mids, starts, lens, cum, _ = self.tables[p]
        i = bisect_right(mids, t) - 1
        return cum[i] + min(max(t - starts[i], 0), lens[i])

    def plus(self, p, t, secs):
        if secs <= 0:
            return t
        mids, starts, lens, cum, ends = self.tables[p]
        goal = self.position(p, t) + secs
        i = bisect_left(ends, goal)
        return starts[i] + goal - cum[i]


def epoch(ts):
    return int(ts.replace(tzinfo=UTC).timestamp()) if ts is not None and not pd.isna(ts) else None


# ------------------------------------------------------------------ main validation
def validate(data_root, out_dir, source_only):
    start_ts = dt.datetime.now(UTC).replace(microsecond=0).isoformat()
    crm, gt = data_root / "crm", data_root / "crm_ground_truth"
    fields = design("crm_field_dictionary.csv")
    ents = design("crm_entity_catalog.csv")
    params = dict(zip(*[design("synthetic_generation_parameters.csv")[c] for c in ("parameter_name", "value")]))
    vol = design("synthetic_volume_specification.csv").set_index("item")
    trans = design("lead_transition_rules.csv")
    stat = design("lead_status_catalog.csv")
    disp = design("lead_disposition_catalog.csv")
    slas = design("sla_rule_catalog.csv").set_index("sla_id")
    caps = design("rm_capacity_rule_catalog.csv").set_index("rule_id")
    spc = design("specialist_rule_catalog.csv")
    dups = design("duplicate_rule_catalog.csv").set_index("rule_id")
    rts = design("routing_rule_catalog.csv")
    cal = BusinessCalendar(design("business_calendar.csv"))
    period_start = dt.datetime.fromisoformat(params["period_start_utc"])
    period_end = dt.datetime.fromisoformat(params["period_end_utc"])
    cutoff = epoch(period_end)
    terminal = set(stat[stat.is_terminal == "true"].status_code)
    tables = list(ents.table_name)
    con = duckdb.connect()
    con.execute(f"attach '{WAREHOUSE.as_posix()}' as wh (read_only)")

    # ---------------- inventory
    G = "entity_inventory"
    print("Inventory")
    man_path = crm / "synthetic_crm_manifest.csv"
    man = pd.read_csv(man_path, dtype=str, keep_default_na=False) if man_path.exists() else pd.DataFrame(columns=["entity_name"])
    ent_man = man[man.get("file_role", pd.Series(dtype=str)) == "entity"] if len(man) else man
    check(G, "every catalogued entity has exactly one manifest entry and file", sorted(ent_man.entity_name) == sorted(tables)
          and all((crm / f"{t}.parquet").exists() for t in tables), f"{len(ent_man)} of {len(tables)} entities")
    bad_hash = [r.file_name for r in man.itertuples() if not (crm / r.file_name).exists() or sha(crm / r.file_name) != r.sha256]
    check(G, "manifest SHA-256 hashes match the files", not bad_hash, bad_hash or f"{len(man)} files")
    for t in tables:
        con.execute(f"create or replace view {t} as select * from read_parquet('{(crm / (t + '.parquet')).as_posix()}')")
    counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
    bad_rows = [r.entity_name for r in ent_man.itertuples() if int(r.row_count) != counts[r.entity_name]]
    check(G, "manifest row counts match the files", not bad_rows, bad_rows or "all match")
    meta_ok = all(r.generator_version == params["generator_version"] and r.master_random_seed == params["random_seed"]
                  and r.schema_version == params["schema_version"] for r in man.itertuples())
    check(G, "manifest records generator version, master seed and schema version from the parameters catalog", meta_ok and len(man) > 0,
          f"version {params['generator_version']}, seed {params['random_seed']}")
    for t, item in VOLUME_ITEM.items():
        lo, hi = float(vol.loc[item, "range_low"]), float(vol.loc[item, "range_high"])
        results[G].append({"check": f"volume {t} within {int(lo)}-{int(hi)}", "passed": lo <= counts[t] <= hi, "detail": counts[t]})
        print(f"  [{'PASS' if lo <= counts[t] <= hi else 'FAIL'}] volume {t}: {counts[t]} ({int(lo)}-{int(hi)})")
    gman = pd.read_csv(gt / "ground_truth_manifest.csv", dtype=str) if (gt / "ground_truth_manifest.csv").exists() else pd.DataFrame()
    check(G, "ground truth is stored separately with its own manifest and hashes", len(gman) > 0 and all(sha(gt / r.file_name) == r.sha256 for r in gman.itertuples())
          and not any(p.name.startswith(("lead_truth", "lead_scenario", "prospect_truth", "routing_decision_truth")) for p in crm.rglob("*")),
          f"{len(gman)} files")
    for r in gman.itertuples():
        con.execute(f"create or replace view gt_{r.file_name.split('.')[0]} as select * from read_parquet('{(gt / r.file_name).as_posix()}')")

    # ---------------- schema
    G = "schema_validation"
    print("\nSchema")
    duck = {"varchar": "VARCHAR", "boolean": "BOOLEAN", "integer": "BIGINT", "date": "DATE", "timestamp": "TIMESTAMP"}
    bad_cols, bad_types, bad_null, bad_acc, bad_pk, bad_pat = {}, {}, {}, {}, {}, {}
    for t in tables:
        f = fields[fields.table_name == t]
        info = con.execute(f"select column_name, column_type from (describe {t})").fetchall()
        if [c for c, _ in info] != list(f.column_name):
            bad_cols[t] = sorted(set(c for c, _ in info) ^ set(f.column_name)) or "order"
        want = {r.column_name: duck.get(r.data_type, r.data_type.upper().replace(" ", "")) for r in f.itertuples()}
        types = {c: ty.replace(" ", "") for c, ty in info}
        wrong = [c for c in want if c in types and types[c] != want[c]]
        if wrong:
            bad_types[t] = wrong
        for r in f.itertuples():
            if r.column_name not in types:
                continue
            if r.nullable == "false":
                n = con.execute(f"select count(*) from {t} where {r.column_name} is null").fetchone()[0]
                if n:
                    bad_null[f"{t}.{r.column_name}"] = n
            if r.accepted_values and r.data_type in ("varchar", "integer"):
                vals = [v for v in r.accepted_values.split(";")]
                lst = ", ".join("'" + v.replace("'", "''") + "'" for v in vals)
                n = con.execute(f"select count(*) from {t} where {r.column_name} is not null and cast({r.column_name} as varchar) not in ({lst})").fetchone()[0]
                if n:
                    bad_acc[f"{t}.{r.column_name}"] = n
        pk = ents.set_index("table_name").loc[t, "primary_key"]
        d = con.execute(f"select count(*) - count(distinct {pk}), count(*) filter (where {pk} is null) from {t}").fetchone()
        if d[0] or d[1]:
            bad_pk[t] = d
    for (t, c), pat in ID_PATTERN.items():
        n = con.execute(f"select count(*) from {t} where not regexp_full_match({c}, '{pat}')").fetchone()[0]
        if n:
            bad_pat[f"{t}.{c}"] = n
    check(G, "columns and column order match the field dictionary", not bad_cols, bad_cols or f"{len(tables)} tables, {len(fields)} fields")
    check(G, "column types match the field dictionary", not bad_types, bad_types or "all types match")
    check(G, "required fields are never null", not bad_null, bad_null or "none null")
    check(G, "values stay inside the accepted-value lists", not bad_acc, bad_acc or "all inside")
    check(G, "primary keys are unique and not null", not bad_pk, bad_pk or f"{len(tables)} primary keys")
    check(G, "identifiers follow their fixed-width fictional patterns", not bad_pat, bad_pat or f"{len(ID_PATTERN)} identifier columns")
    rs = [t for t in tables if con.execute(f"select count(*) from {t} where record_source <> 'synthetic_generator'").fetchone()[0]]
    check(G, "every record is marked record_source = synthetic_generator", not rs, rs or "all records")
    col_names = set(fields.column_name)
    leak = sorted((set(c for t in tables for c, _ in con.execute(f"select column_name, column_type from (describe {t})").fetchall()) - col_names)
                  | (col_names & GROUND_TRUTH_COLUMNS))
    check(G, "operational tables contain only dictionary columns and no ground-truth fields", not leak, leak or "clean")

    # ---------------- referential integrity
    G = "referential_integrity_checks"
    print("\nReferential integrity")
    fk_fail = {}
    for r in fields[fields.foreign_key != ""].itertuples():
        fk = r.foreign_key
        m = re.match(r"(crm_[a-z_]+)\.([a-z_0-9]+)$", fk)
        if m:
            tgt = f"select {m.group(2)} from {m.group(1)}"
        elif fk.startswith("analytics.dim_cma"):
            tgt = "select cma_code_sgc2021 from wh.analytics.dim_cma"
        elif fk.startswith("analytics.dim_industry.industry_code"):
            tgt = "select industry_code from wh.analytics.dim_industry where industry_system = 'NAICS2022_1097'"
        elif fk.startswith("analytics.dim_industry.industry_system"):
            tgt = "select distinct industry_system from wh.analytics.dim_industry"
        elif "dim_size_band" in fk:
            tgt = "select size_band_key from wh.analytics.dim_size_band union all select 'unknown'"
        elif fk.startswith("lead_transition_rules"):
            con.register("tr_catalog", trans)
            tgt = "select transition_id from tr_catalog"
        else:
            continue
        n = con.execute(f"select count(*) from {r.table_name} where {r.column_name} is not null and {r.column_name} not in ({tgt})").fetchone()[0]
        results[G].append({"check": f"{r.table_name}.{r.column_name} -> {fk}", "passed": n == 0, "detail": n})
        if n:
            fk_fail[f"{r.table_name}.{r.column_name}"] = n
    check(G, "all foreign keys resolve (CRM entities, dim_cma, dim_industry, dim_size_band, transition catalog)", not fk_fail, fk_fail or "all resolve")
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    check(G, "every lead references one prospect; every activity, SLA event and outcome references a lead", q("""
        select (select count(*) from crm_lead l left join crm_business_prospect p using (prospect_id) where p.prospect_id is null)
             + (select count(*) from crm_lead_activity a left join crm_lead l using (lead_id) where l.lead_id is null)
             + (select count(*) from crm_opportunity_outcome o left join crm_lead l using (lead_id) where l.lead_id is null)""") == 0)
    n = q("""select count(*) from crm_lead_activity a join crm_lead_assignment s using (assignment_id)
             where a.rm_id is distinct from s.rm_id or a.lead_id <> s.lead_id or s.rm_id is null""")
    check(G, "activities belong to their assignment's lead and RM (never to a queue)", n == 0, n)
    n = q("select count(*) from crm_sla_event where regexp_matches(sla_id, '^SLA-0[5-9]') and assignment_id is null")
    check(G, "assignment-level SLA clocks (SLA-05..SLA-09) reference an assignment", n == 0, n)
    n = q("""select count(*) from crm_lead_source_touch where (channel_code = 'CH-MKT' and campaign_id is null) or (channel_code = 'CH-PTR' and partner_code is null)
             or (channel_code = 'CH-BRN' and referring_branch_code is null) or (channel_code = 'CH-RMP' and prospecting_rm_id is null)""")
    check(G, "channel-specific source identifiers are present (campaign, partner, branch, prospecting RM)", n == 0, n)
    n = q("select count(*) from crm_lead_assignment where (rm_id is null) <> (queue_code is not null)")
    check(G, "queue ownership (queue_code, no RM) is distinguishable from RM ownership", n == 0, n)
    n = q("""select count(*) from crm_rm_territory rt join crm_territory t using (territory_id)
             where t.territory_type = 'cma' and t.evidence_tier is distinct from (select tier from wh.analytics.dim_cma d where d.cma_code_sgc2021 = t.cma_code_sgc2021)""")
    n2 = q("select count(*) from crm_territory t left join wh.analytics.dim_cma d using (cma_code_sgc2021) where t.territory_type = 'cma' and t.evidence_tier is distinct from d.tier")
    check(G, "territory evidence tiers equal analytics.dim_cma tiers", n == 0 and n2 == 0, n2)
    n = q("""select count(*) from crm_rm_specialization s left join (select industry_code, specialist_type from specialist_catalog) c using (industry_code)
             where c.industry_code is null or c.specialist_type <> s.specialist_type""") if con.register("specialist_catalog", spc) else 0
    check(G, "RM specializations use specialist-catalog industries and types", n == 0, n)
    n = q("""select count(*) from crm_opportunity_outcome o where not exists (select 1 from crm_lead_status_history h where h.lead_id = o.lead_id and h.to_status = 'QUALIFIED')""")
    check(G, "every opportunity belongs to a lead that passed through QUALIFIED", n == 0, n)
    n = q("""select count(*) from crm_rm_capacity_snapshot s join crm_relationship_manager r using (rm_id)
             where s.snapshot_date < r.employment_start_date or (r.employment_end_date is not null and s.snapshot_date >= r.employment_end_date)""")
    check(G, "capacity snapshots exist only while the RM is employed", n == 0, n)

    # ---------------- lifecycle and status transitions
    G = "status_transition_checks"
    print("\nLifecycle")
    con.register("transitions", trans)
    con.register("statuses", stat)
    con.register("dispositions", disp)
    con.execute("""create or replace temp table hist as select *, row_number() over (partition by lead_id order by changed_at, status_history_id) rn,
                   lag(to_status) over (partition by lead_id order by changed_at, status_history_id) prev_to from crm_lead_status_history""")
    n = q("""select count(*) from hist h left join transitions t on t.from_status = h.from_status and t.to_status = h.to_status
             where h.rn > 1 and (t.transition_id is null or t.transition_id <> h.transition_id or t.trigger_event <> h.event_code)""")
    check(G, "every status change uses an allowed transition with its catalogued transition id and trigger event", n == 0, n)
    n = q("select count(*) from hist where (rn = 1) <> (from_status is null) or (rn = 1 and (to_status <> 'NEW' or event_code <> 'LEAD_CAPTURED'))")
    n2 = q("select count(*) from hist where rn > 1 and from_status is distinct from prev_to")
    check(G, "each lead's history starts NEW (LEAD_CAPTURED) and chains without gaps", n == 0 and n2 == 0, f"{n} bad starts, {n2} breaks")
    n = q("""select count(*) from crm_lead l join (select lead_id, to_status from hist qualify rn = max(rn) over (partition by lead_id)) h using (lead_id)
             where l.current_lead_status <> h.to_status""")
    check(G, "current lead status equals the latest status-history row", n == 0, n)
    n = q("""select count(*) from crm_lead l left join dispositions d on d.disposition_code = l.disposition_code
             where (l.current_lead_status in (select status_code from statuses where is_terminal = 'true')) <> (l.disposition_code is not null)
                or (l.disposition_code is not null and d.applies_to_status <> l.current_lead_status)""")
    n2 = q("""select count(*) from hist h left join dispositions d on d.disposition_code = h.disposition_code
              where (h.to_status in (select status_code from statuses where is_terminal = 'true')) <> (h.disposition_code is not null)
                 or (h.disposition_code is not null and d.applies_to_status <> h.to_status)""")
    check(G, "terminal statuses carry a disposition that applies to them; non-terminal statuses carry none", n == 0 and n2 == 0, f"{n} leads, {n2} history rows")
    n = q("""select count(*) from hist h join transitions t using (transition_id)
             where (t.transition_type = 'audited_reopening') <> (h.reopen_approver_role is not null)
                or (t.transition_type = 'audited_reopening' and (h.status_reason is null or h.changed_at - (select max(x.changed_at) from hist x
                    where x.lead_id = h.lead_id and x.rn < h.rn and x.to_status = h.from_status) > interval 180 days))""")
    check(G, "audited reopenings carry approver role and reason, within 180 days of closure; no other row has an approver", n == 0, n)
    n = q("select count(*) from hist where transition_id in ('T-01', 'T-15', 'T-28', 'T-29') and status_reason is null")
    check(G, "validation failures, releases to queue and reopenings record a status reason", n == 0, n)
    n = q("select count(*) from crm_lead where (current_lead_status = 'MERGED') <> (surviving_lead_id is not null)")
    check(G, "surviving_lead_id is present exactly for merged leads", n == 0, n)
    n = q("""select count(*) from crm_lead l where current_lead_status in ('ASSIGNED', 'CONTACTED', 'NURTURING', 'QUALIFIED')
             and (select count(*) from crm_lead_assignment a where a.lead_id = l.lead_id and a.active_flag and a.assignment_role = 'primary' and a.rm_id is not null) <> 1""")
    n2 = q("""select count(*) from (select lead_id from crm_lead_assignment where active_flag and assignment_role = 'primary' group by 1 having count(*) > 1)""")
    check(G, "at most one active primary assignment per lead; exactly one RM owner for leads in active work", n == 0 and n2 == 0, f"{n} without owner, {n2} with several")
    n = q("""select count(*) from crm_lead l join crm_lead_assignment a using (lead_id)
             where a.active_flag and l.current_lead_status in (select status_code from statuses where is_terminal = 'true')""")
    check(G, "terminal leads have no active assignment", n == 0, n)
    n = q("""select count(*) from crm_lead_assignment where active_flag <> (assignment_status in ('QUEUED', 'ASSIGNED', 'ACKNOWLEDGED', 'REASSIGNMENT_PENDING'))
             or (active_flag = (ended_at is not null))
             or (assignment_status = 'ACKNOWLEDGED' and acknowledged_at is null)
             or (assignment_status = 'DECLINED' and (declined_at is null or decline_reason is null or reassignment_reason <> 'decline'))
             or (assignment_status in ('REASSIGNED', 'REASSIGNMENT_PENDING') and (reassignment_triggered_at is null or reassignment_reason is null))
             or ((declined_at is not null) <> (decline_reason is not null)) or ((reassignment_triggered_at is not null) <> (reassignment_reason is not null))""")
    check(G, "assignment status, active flag and lifecycle timestamps are consistent", n == 0, n)
    n = q("""select count(*) from crm_lead_assignment where manual_override_flag and (override_reason_code is null or override_user_role is null)
             or (not manual_override_flag and (override_reason_code is not null or override_user_role is not null))
             or (capacity_exception_flag and not manual_override_flag)""")
    check(G, "manual overrides have a reason code and actor role; capacity exceptions only with an override", n == 0, n)
    n = q("""select count(*) from crm_lead_assignment where (assignment_reason = 'reassignment' and prior_assignment_id is null)
             or (prior_assignment_id is not null and assignment_reason not in ('reassignment', 'override'))
             or ((prior_assignment_id is null) <> (prior_rm_id is null))""")
    n2 = q("""select count(*) from crm_lead_assignment a join crm_lead_assignment p on p.assignment_id = a.prior_assignment_id
              where p.lead_id <> a.lead_id or p.rm_id is distinct from a.prior_rm_id or p.active_flag or p.assignment_status not in ('REASSIGNED', 'DECLINED')""")
    check(G, "reassignments preserve the prior assignment and prior RM; prior assignments are ended as REASSIGNED or DECLINED", n == 0 and n2 == 0, f"{n}, {n2}")
    n = q("""select count(*) from crm_lead_assignment a left join crm_routing_decision d using (routing_decision_id)
             where (a.assignment_reason <> 'override' and a.routing_decision_id is null) or a.rule_version_id <> 'RV-ROUTING-001'
                or (d.routing_decision_id is null and a.routing_decision_id is not null)""")
    check(G, "assignments preserve their routing decision and routing rule version", n == 0, n)
    con.register("routing_rules", rts)
    bad_trace = q("""select count(*) from (select unnest(string_split(rule_trace, ';')) r from crm_routing_decision) where r not in (select rule_id from routing_rules)""")
    n = q("""select count(*) from crm_routing_decision where (decision_type in ('initial', 'queue_release') and lead_status_at_decision <> 'READY_FOR_ASSIGNMENT')
             or ((routing_outcome = 'queued_manual_review') <> (manual_review_reason is not null))
             or ((selected_rm_id is null) <> (routing_outcome like 'queued%'))
             or (routing_outcome like 'queued%' and (territory_match_flag is not null or segment_match_flag is not null))""")
    check(G, "routing decisions: status at decision, rule traces from the routing catalog, queue and manual-review fields consistent", n == 0 and bad_trace == 0,
          f"{n} inconsistent, {bad_trace} unknown rules")
    n = q("""select count(*) from crm_routing_decision d join crm_lead l using (lead_id)
             where (d.market_scenario_id is null) <> (l.market_evidence_flag <> 'full_framework')""")
    check(G, "market scenario is recorded only for leads with full market evidence", n == 0, n)
    con.register("duprules", dups.reset_index())
    n = q("""select count(*) from crm_duplicate_group g join duprules r on r.rule_id = g.match_rule_id where r.match_tier <> g.match_tier
             or g.member_count <> 2 or (g.match_tier = 'review' and g.resolved_at is not null and g.review_decision is null)
             or (g.match_tier <> 'review' and g.review_decision is not null)""")
    n2 = q("""select count(*) from crm_lead l join crm_duplicate_group g using (duplicate_group_id)
              where (l.current_lead_status = 'MERGED') <> (g.surviving_lead_id is not null and (g.match_tier <> 'review' or g.review_decision = 'confirmed'))
                 or (l.current_lead_status = 'MERGED' and l.surviving_lead_id <> g.matched_lead_id)""")
    n3 = q("""select count(*) from crm_duplicate_group g join crm_lead m on m.lead_id = g.matched_lead_id join crm_lead f on f.duplicate_group_id = g.duplicate_group_id
              where m.captured_at >= f.captured_at or m.current_lead_status = 'MERGED'""")
    n4 = q("""select count(*) from hist where to_status = 'MERGED' and lead_id in (select l.lead_id from crm_lead l join crm_duplicate_group g using (duplicate_group_id)
              where g.match_tier = 'review') and from_status <> 'DUPLICATE_REVIEW'""")
    check(G, "duplicates: tiers match their rules; only exact and high-confidence groups auto-merge; review groups merge only when confirmed; survivor is the earlier matched lead",
          n + n2 + n3 + n4 == 0, f"{n}, {n2}, {n3}, {n4}")
    n = q("""select count(*) from crm_lead l where current_lead_status <> 'MERGED'
             and (select count(*) from crm_lead_source_touch t where t.lead_id = l.lead_id and t.is_first_touch) <> 1""")
    n2 = q("""select count(*) from crm_lead l where (select count(*) from crm_lead_source_touch t where t.original_lead_id = l.lead_id
              and t.channel_code = l.first_channel_code and t.touched_at = l.captured_at) <> 1""")
    n3 = q("""select count(*) from crm_lead_source_touch t join crm_lead o on o.lead_id = t.original_lead_id
              where t.lead_id <> t.original_lead_id and not (o.current_lead_status = 'MERGED' and o.surviving_lead_id = t.lead_id)""")
    n4 = q("select count(*) from crm_lead_source_touch t join crm_lead l using (lead_id) where l.current_lead_status = 'MERGED'")
    check(G, "source attribution: each lead keeps its original touch; merged leads' touches move to the survivor unchanged; one first touch per surviving lead",
          n + n2 + n3 + n4 == 0, f"{n}, {n2}, {n3}, {n4}")
    n = q("select count(*) from crm_lead where priority_band <> 'P2'")
    check(G, "every lead carries the default priority band P2 (A-009; no priority model exists)", n == 0, n)
    n = q("""select count(*) from crm_lead l where stated_service_language = 'not stated' and exists
             (select 1 from crm_routing_decision d where d.lead_id = l.lead_id and d.language_match_flag is not null)""")
    check(G, "language is used in routing only when explicitly stated", n == 0, n)

    # ---------------- temporal consistency and recomputation
    G = "temporal_consistency_checks"
    print("\nTemporal consistency")
    planned = {("crm_sla_event", "target_at"), ("crm_sla_event", "warning_at"), ("crm_lead_activity", "next_contact_due_at")}
    ts_cols = [(r.table_name, r.column_name) for r in fields.itertuples() if r.data_type == "timestamp" and (r.table_name, r.column_name) not in planned]
    out_of_range = {}
    for t, c in ts_cols:
        n = q(f"select count(*) from {t} where {c} is not null and ({c} < timestamp '{period_start}' or {c} >= timestamp '{period_end}')")
        if n and not (t == "crm_rule_version" and c == "effective_from"):
            out_of_range[f"{t}.{c}"] = n
    check(G, "every event timestamp lies inside the synthetic period [2025-06-01, 2026-06-01) UTC (planned deadlines and next-contact dates may fall later)",
          not out_of_range, out_of_range or f"{len(ts_cols)} event timestamp columns")
    n = q("select count(*) from crm_lead l join crm_business_prospect p using (prospect_id) where p.created_at > l.captured_at")
    check(G, "prospect records are created no later than their leads", n == 0, n)
    n = q("select count(*) from hist h join crm_lead l using (lead_id) where h.changed_at < l.captured_at or (h.rn = 1 and h.changed_at <> l.captured_at)")
    n2 = q("select count(*) from hist where rn > 1 and changed_at < (select max(changed_at) from hist x where x.lead_id = hist.lead_id and x.rn < hist.rn)")
    check(G, "status history starts at capture and is non-decreasing", n == 0 and n2 == 0, f"{n}, {n2}")
    n = q("""select count(*) from crm_lead_assignment a join crm_routing_decision d using (routing_decision_id) where a.assigned_at < d.decided_at""")
    n2 = q("""select count(*) from crm_routing_decision d where decision_type = 'initial' and d.decided_at < (select min(changed_at) from hist h
              where h.lead_id = d.lead_id and h.to_status = 'READY_FOR_ASSIGNMENT')""")
    check(G, "validation precedes routing, and routing precedes assignment", n == 0 and n2 == 0, f"{n}, {n2}")
    n = q("""select count(*) from crm_lead_assignment where acknowledged_at < assigned_at or declined_at < assigned_at or reassignment_triggered_at < assigned_at
             or ended_at < assigned_at or ended_at < acknowledged_at or ended_at < reassignment_triggered_at""")
    n2 = q("""select count(*) from crm_lead_assignment a join crm_lead_assignment p on p.assignment_id = a.prior_assignment_id
              where a.assigned_at < coalesce(p.reassignment_triggered_at, p.declined_at, p.assigned_at) or a.assigned_at < p.assigned_at""")
    check(G, "assignment timestamps are ordered; reassignment follows the prior assignment", n == 0 and n2 == 0, f"{n}, {n2}")
    n = q("""select count(*) from crm_lead_activity a join crm_lead_assignment s using (assignment_id)
             where a.activity_at < s.assigned_at or (s.ended_at is not null and a.activity_at > s.ended_at)""")
    check(G, "activities occur while their assignment is active (assignment precedes RM-owned activity)", n == 0, n)
    n = q("""select count(*) from crm_lead_activity a join crm_lead l using (lead_id) where not l.consent_to_contact_flag""")
    check(G, "contact activity only for leads with consent to contact", n == 0, n)
    n = q("""select count(*) from crm_opportunity_outcome o where o.opened_at <> (select min(changed_at) from hist h where h.lead_id = o.lead_id and h.to_status = 'QUALIFIED')
             or (o.closed_at is not null) <> (o.opportunity_status <> 'open') or o.closed_at < o.opened_at""")
    n2 = q("""select count(*) from hist c where c.to_status = 'CONVERTED' and not exists (select 1 from crm_opportunity_outcome o where o.lead_id = c.lead_id
              and o.opened_at <= c.changed_at and o.opportunity_status = 'won' and o.closed_at = c.changed_at)""")
    check(G, "qualification precedes (equals) opportunity creation; opportunity precedes conversion", n == 0 and n2 == 0, f"{n}, {n2}")
    n = q("""select count(*) from crm_lead l where current_lead_status in (select status_code from statuses where is_terminal = 'true')
             and (select max(activity_at) from crm_lead_activity a where a.lead_id = l.lead_id) > l.updated_at""")
    check(G, "terminal closure never precedes the lead's final activity", n == 0, n)
    # SLA recomputation on the independent calendar
    s = con.execute("""select e.*, l.province_code from crm_sla_event e join crm_lead l using (lead_id)""").df()
    pk = s.province_code.where(s.province_code.isin(list(cal.tables)), None)
    bad_target = bad_outcome = bad_elapsed = bad_role = bad_stop = bad_reason = 0
    for r, p in zip(s.itertuples(), pk):
        rule = slas.loc[r.sla_id]
        unit = 3600 if rule.target_unit == "hours" else (int(8.5 * 3600) if rule.time_basis == "business" else 86400)
        secs = int(float(rule.target_value) * unit)
        st = epoch(r.started_at)
        pause = int(r.paused_minutes) * 60
        if rule.time_basis == "business":
            tg, wn = cal.plus(p, st, secs + pause), cal.plus(p, st, secs * int(rule.warning_pct) // 100 + pause)
        else:
            tg, wn = st + secs + pause, st + secs * int(rule.warning_pct) // 100 + pause
        if tg != epoch(r.target_at) or wn != epoch(r.warning_at):
            bad_target += 1
        sp = epoch(r.stopped_at)
        end = sp if sp is not None else cutoff
        el = (cal.position(p, end) - cal.position(p, st)) if rule.time_basis == "business" else end - st
        if int(r.elapsed_business_minutes) != max(0, el // 60 - int(r.paused_minutes)):
            bad_elapsed += 1
        if r.sla_outcome != "not_applicable":
            exp = ("met" if sp <= tg else "breached_closed") if sp is not None else ("breached_open" if cutoff >= tg else ("warning_open" if cutoff >= wn else "open"))
            if exp != r.sla_outcome:
                bad_outcome += 1
        if r.responsible_role != rule.responsible_role:
            bad_role += 1
        if sp is not None and r.stop_event_code not in rule.stop_events.split(";"):
            bad_stop += 1
        if (r.sla_outcome == "not_applicable" or int(r.paused_minutes) > 0) != (r.exception_reason is not None and not pd.isna(r.exception_reason)):
            bad_reason += 1
    check(G, "SLA target and warning times recompute from start, rule and business calendar", bad_target == 0, f"{bad_target} of {len(s)}")
    check(G, "SLA outcomes and elapsed business minutes recompute (cut-off 2026-06-01T00:00Z)", bad_outcome == 0 and bad_elapsed == 0, f"{bad_outcome} outcomes, {bad_elapsed} elapsed")
    check(G, "SLA clocks stop only on their rule's stop events; responsible role copied from the rule; exceptions carry a reason",
          bad_stop == 0 and bad_role == 0 and bad_reason == 0, f"{bad_stop}, {bad_role}, {bad_reason}")
    n = q("""select count(*) from crm_sla_event e join crm_lead l using (lead_id) where e.stopped_at is null and e.sla_id <> 'SLA-10'
             and l.current_lead_status in (select status_code from statuses where is_terminal = 'true')""")
    n2 = q("""select count(*) from crm_sla_event where sla_outcome like 'breached%' and regexp_matches(sla_id, '^SLA-0(4|5|6|8A|9)') and sla_id <> 'SLA-08B' and escalation_level < 1""")
    check(G, "terminal leads have no open SLA clocks; breached assignment, first-contact, inactivity and reassignment clocks are escalated", n == 0 and n2 == 0, f"{n}, {n2}")
    # capacity recomputation (independent SQL)
    w_new, w_con, w_q, w_sup = (float(caps.loc[k, "weight"]) for k in ("CAP-01", "CAP-02", "CAP-03", "CAP-04"))
    m = re.search(r"more than (\d+) days away weigh ([0-9.]+)", caps.loc["CAP-02", "exception_handling"])
    far_days, w_far = int(m.group(1)), float(m.group(2))
    base = float(caps.loc["CAP-05", "threshold"])
    hard, warning, ceiling = float(caps.loc["CAP-06", "hard_limit"]), float(caps.loc["CAP-06", "warning_level"]), float(caps.loc["CAP-09", "hard_limit"])
    con.execute(f"""create or replace temp table recomputed as
        with act as (select s.capacity_snapshot_id, s.snapshot_as_of_utc t, a.lead_id, a.assignment_role
                     from crm_rm_capacity_snapshot s join crm_lead_assignment a on a.rm_id = s.rm_id and a.assigned_at < s.snapshot_as_of_utc
                     and (a.ended_at is null or a.ended_at >= s.snapshot_as_of_utc)),
             hh as (select lead_id, changed_at, arg_max(to_status, status_history_id) to_status from crm_lead_status_history group by 1, 2),
             st as (select act.*, h.to_status from act asof join hh h on h.lead_id = act.lead_id and h.changed_at < act.t),
             nt as (select lead_id, activity_at, arg_max(next_contact_due_at, activity_id) next_contact_due_at from crm_lead_activity
                    where activity_type = 'nurture_task' group by 1, 2),
             nu as (select st.*, n.next_contact_due_at from st asof left join nt n on n.lead_id = st.lead_id and n.activity_at < st.t)
        select capacity_snapshot_id,
               sum(case when assignment_role = 'specialist_support' then {w_sup}
                        when to_status = 'ASSIGNED' then {w_new}
                        when to_status = 'CONTACTED' then {w_con}
                        when to_status = 'NURTURING' then case when next_contact_due_at > t + interval {far_days} days then {w_far} else {w_con} end
                        when to_status = 'QUALIFIED' then {w_q} else 0 end) wl,
               sum(case when assignment_role = 'specialist_support' then {w_sup} else 0 end) sup
        from nu group by 1""")
    n = q(f"""select count(*) from crm_rm_capacity_snapshot s left join recomputed r using (capacity_snapshot_id)
              where abs(s.weighted_workload_points - coalesce(r.wl, 0)) > 1e-6 or abs(s.specialist_support_points - coalesce(r.sup, 0)) > 1e-6
                 or s.snapshot_as_of_utc <> cast(s.snapshot_date as timestamp)
                 or s.available_capacity_points <> {base} * case s.availability_status when 'available' then 1 when 'partial' then 0.5 else 0 end
                 or (s.utilization_ratio is null) <> (s.available_capacity_points = 0)
                 or abs(s.utilization_ratio - s.weighted_workload_points / nullif(s.available_capacity_points, 0)) > 0.00005 + 1e-9""")
    check(G, "capacity snapshots recompute from assignments, lead statuses, nurture dates and CAP weights (as of 00:00 UTC)", n == 0, f"{n} of {counts['crm_rm_capacity_snapshot']}")
    n = q(f"""select count(*) from crm_rm_capacity_snapshot where capacity_state <> case
              when utilization_ratio is null then 'unavailable'
              when utilization_ratio >= {hard} and capacity_state = 'approved_exception' and utilization_ratio <= {ceiling} then 'approved_exception'
              when utilization_ratio >= {hard} then 'at_or_above_hard_limit' when utilization_ratio >= {warning} then 'warning' else 'below_warning' end""")
    n2 = q(f"""select count(*) from crm_rm_capacity_snapshot s where capacity_state = 'approved_exception' and not exists (select 1 from crm_lead_assignment a
               where a.rm_id = s.rm_id and a.capacity_exception_flag and a.assigned_at <= s.snapshot_as_of_utc and a.assigned_at > s.snapshot_as_of_utc - interval 10 days)""")
    check(G, "capacity states follow the CAP-06 thresholds; approved exceptions follow an override with a capacity exception (CAP-09)", n == 0 and n2 == 0, f"{n}, {n2}")
    n = q(f"""select count(*) from crm_routing_decision d asof join crm_rm_capacity_snapshot s on s.rm_id = d.selected_rm_id and s.snapshot_as_of_utc <= d.decided_at
              where d.selected_rm_id is not null and (d.capacity_utilization_at_decision is distinct from s.utilization_ratio
                 or (d.routing_outcome <> 'override_assigned' and (s.utilization_ratio >= {hard} or s.availability_status in ('planned_leave', 'unavailable'))))""")
    n2 = q("""select count(*) from crm_routing_decision d where selected_rm_id is not null and not exists (select 1 from crm_rm_capacity_snapshot s
              where s.rm_id = d.selected_rm_id and s.snapshot_as_of_utc <= d.decided_at)""")
    check(G, "routing uses the capacity snapshot available at decision time; no ordinary assignment to an unavailable or hard-limit RM", n == 0 and n2 == 0, f"{n}, {n2}")

    # ---------------- scenarios (observable predicates)
    G = "scenario_coverage"
    print("\nScenario coverage")
    mand = tuple(spc[spc.requirement_level == "mandatory"].industry_code)
    prefd = tuple(spc[spc.requirement_level == "preferred"].industry_code)
    has_rm = "exists (select 1 from crm_lead_assignment a where a.lead_id = l.lead_id and a.rm_id is not null and a.assignment_role = 'primary')"
    in_hist = lambda frm, to: f"exists (select 1 from hist h where h.lead_id = l.lead_id and h.from_status = '{frm}' and h.to_status = '{to}')"  # noqa: E731
    sla_b = lambda sid: f"exists (select 1 from crm_sla_event e where e.lead_id = l.lead_id and e.sla_id like '{sid}' and e.sla_outcome like 'breached%')"  # noqa: E731
    con.execute(f"""create or replace temp table cover_breach as
        select distinct d.lead_id, s.utilization_ratio from crm_routing_decision d join crm_lead l using (lead_id)
        join crm_rm_territory rt on rt.territory_id = l.territory_id and rt.valid_from <= cast(d.decided_at as date) and (rt.valid_to is null or rt.valid_to > cast(d.decided_at as date))
        asof join crm_rm_capacity_snapshot s on s.rm_id = rt.rm_id and s.snapshot_as_of_utc <= d.decided_at
        where d.selected_rm_id is not null and rt.rm_id <> d.selected_rm_id and s.utilization_ratio >= {hard}""")
    pred = {
        "SC-01": f"""l.first_channel_code = 'CH-DIG' and {has_rm} and exists (select 1 from hist h where h.lead_id = l.lead_id and h.to_status = 'CONTACTED')
                     and not exists (select 1 from crm_lead_assignment a where a.lead_id = l.lead_id and a.prior_assignment_id is not null)
                     and not {sla_b('SLA-06%')} and not exists (select 1 from hist h where h.lead_id = l.lead_id and h.to_status = 'VALIDATION_PENDING')""",
        "SC-02": f"l.first_channel_code = 'CH-BRN' and {has_rm}", "SC-03": f"l.first_channel_code = 'CH-MKT' and {has_rm}",
        "SC-04": f"l.first_channel_code = 'CH-PTR' and {has_rm}", "SC-05": f"l.first_channel_code = 'CH-RMP' and {has_rm}",
        "SC-06": in_hist("VALIDATION_PENDING", "READY_FOR_ASSIGNMENT"), "SC-07": "l.current_lead_status = 'INVALID'",
        "SC-08": "l.current_lead_status = 'MERGED' and (select match_tier from crm_duplicate_group g where g.duplicate_group_id = l.duplicate_group_id) <> 'review'",
        "SC-09": in_hist("DUPLICATE_REVIEW", "MERGED"),
        "SC-10": """exists (select 1 from crm_lead_assignment q join crm_lead_assignment a on a.lead_id = q.lead_id and a.rm_id is not null and a.assigned_at >= q.assigned_at
                    where q.lead_id = l.lead_id and q.rm_id is null)""",
        "SC-11": "l.lead_id in (select lead_id from cover_breach)",
        "SC-12": "exists (select 1 from crm_lead_assignment a where a.lead_id = l.lead_id and a.reassignment_reason = 'rm_unavailable')",
        "SC-13": "exists (select 1 from crm_routing_decision d where d.lead_id = l.lead_id and d.territory_match_flag = false)",
        "SC-14": f"""(select industry_code from crm_business_prospect p where p.prospect_id = l.prospect_id) in {mand + prefd} and exists (select 1 from crm_routing_decision d
                     where d.lead_id = l.lead_id and (d.specialist_match_flag = false or d.routing_outcome = 'queued_specialist'
                     or d.selected_rm_id in (select rm_id from crm_relationship_manager where home_territory_id = 'TER-NATIONAL-SPECIALIST')))""",
        "SC-15": "l.stated_service_language in ('en', 'fr') and exists (select 1 from crm_routing_decision d where d.lead_id = l.lead_id and d.language_match_flag is not null)",
        "SC-16": """exists (select 1 from crm_sla_event e join crm_lead_assignment a using (assignment_id) where e.lead_id = l.lead_id and e.sla_id = 'SLA-05'
                    and e.sla_outcome like 'breached%' and a.assignment_role = 'primary')""",
        "SC-17": f"{sla_b('SLA-06%')} and exists (select 1 from hist h where h.lead_id = l.lead_id and h.to_status = 'CONTACTED')",
        "SC-18": "exists (select 1 from hist h where h.lead_id = l.lead_id and h.transition_id = 'T-20' and h.disposition_code = 'DSP-NO-RESPONSE')",
        "SC-19": sla_b("SLA-08A"),
        "SC-20": """exists (select 1 from crm_lead_assignment a where a.lead_id = l.lead_id and a.reassignment_reason in
                    ('acknowledgement_breach', 'first_contact_breach', 'inactivity'))""",
        "SC-21": "exists (select 1 from crm_lead_assignment a where a.lead_id = l.lead_id and a.manual_override_flag)",
        "SC-22": in_hist("NURTURING", "CONTACTED"),
        "SC-23": "exists (select 1 from crm_opportunity_outcome o where o.lead_id = l.lead_id)", "SC-24": in_hist("QUALIFIED", "CONVERTED"),
        "SC-25": in_hist("CONTACTED", "CLOSED_LOST"), "SC-26": in_hist("ASSIGNED", "WITHDRAWN"),
        "SC-27": f"l.geography_status = 'resolved_province_remainder' and {has_rm}",
        "SC-28": f"l.market_evidence_flag = 'scale_only_limited_evidence' and {has_rm}",
        "SC-29": f"l.market_evidence_flag = 'no_market_context' and {has_rm}",
        "SC-30": f"""exists (select 1 from crm_routing_decision d where d.lead_id = l.lead_id and d.capacity_utilization_at_decision = {warning})
                     or l.lead_id in (select lead_id from cover_breach where utilization_ratio = {hard})""",
        "SC-31": """(select count(*) from crm_lead_assignment a where a.lead_id = l.lead_id and a.rm_id is not null and a.assignment_role = 'primary') >= 3
                    and (select count(*) from crm_lead_assignment a where a.lead_id = l.lead_id and a.active_flag and a.assignment_role = 'primary') <= 1""",
        "SC-32": """exists (select 1 from crm_lead_source_touch t join crm_lead o on o.lead_id = t.original_lead_id where t.lead_id = l.lead_id
                    and t.original_lead_id <> l.lead_id and t.channel_code <> l.first_channel_code)""",
        "SC-33": "exists (select 1 from hist h where h.lead_id = l.lead_id and h.transition_id in ('T-28', 'T-29'))",
    }
    scat = design("synthetic_scenario_catalog.csv").set_index("scenario_id")
    truth = con.execute("select scenario_id, list(lead_id) from gt_lead_scenario_membership group by 1").fetchall() if "gt_lead_scenario_membership" in \
        {r[0] for r in con.execute("select view_name from duckdb_views()").fetchall()} else []
    truth = {s: set(ls) for s, ls in truth}
    n_leads = counts["crm_lead"]
    short, disagree, rows = [], [], []
    for sc in sorted(pred):
        ids = {r[0] for r in con.execute(f"select l.lead_id from crm_lead l where {pred[sc]}").fetchall()}
        need = int(params[f"min_count_{sc}"])
        tset = truth.get(sc, set())
        jac = len(ids & tset) / len(ids | tset) if ids | tset else 1.0
        lo, hi = (float(x) for x in scat.loc[sc, "indicative_share_pct_range"].split("-"))
        share = 100 * len(ids) / n_leads
        ok = len(ids) >= need
        if not ok:
            short.append(sc)
        if sc in SCENARIO_EXACT and ids != tset:
            disagree.append(sc)
        rows.append({"check": f"{sc} {scat.loc[sc, 'scenario_name']}", "passed": ok,
                     "detail": f"observed {len(ids)} (minimum {need}); share {share:.2f}% vs indicative {lo:g}-{hi:g}% "
                               f"({'inside' if lo <= share <= hi else 'outside, report only'}); ground-truth agreement {jac:.3f}"})
    results[G].extend(rows)
    check(G, "all 33 approved scenarios are observable in the operational data at or above their declared minimum counts", not short, short or "33 of 33")
    check(G, "scenarios with directly observable definitions match the ground-truth labels exactly", not disagree, disagree or f"{len(SCENARIO_EXACT)} scenarios")
    n = q("select count(distinct scenario_id) from gt_lead_scenario_membership") if truth else 0
    check(G, "ground truth labels cover all 33 scenarios", n == 33, n)

    # ---------------- distributions
    G = "distribution_validation"
    print("\nDistributions (validation only; not business KPIs)")
    rates = {
        "duplicate rate": ("select avg((current_lead_status = 'MERGED')::int) from crm_lead", "all leads"),
        "invalid-lead rate": ("select avg((current_lead_status = 'INVALID')::int) from crm_lead", "all leads"),
        "reassignment rate": ("""select avg(r) from (select lead_id, max((prior_assignment_id is not null)::int) r from crm_lead_assignment
                                 where rm_id is not null and assignment_role = 'primary' group by 1)""", "leads with at least one primary RM assignment"),
        "first-contact SLA-breach rate": ("""select avg((sla_outcome like 'breached%')::int) from crm_sla_event where sla_id like 'SLA-06%'
                                             and sla_outcome not in ('open', 'warning_open', 'not_applicable')""", "SLA-06 clocks that are closed or breached"),
        "lead-to-conversion rate": ("select avg((current_lead_status = 'CONVERTED')::int) from crm_lead where current_lead_status not in ('INVALID', 'MERGED')",
                                    "valid leads (not INVALID or MERGED), including leads still open at the cut-off"),
    }
    rate_vals = {}
    for item, (sql, denom) in rates.items():
        v = q(sql)
        rate_vals[item] = round(float(v), 6)
        lo, hi = (float(vol.loc[item, c].rstrip("%")) / 100 for c in ("range_low", "range_high"))
        results[G].append({"check": f"{item} within {lo:.0%}-{hi:.0%}", "passed": lo <= v <= hi, "detail": f"{v:.4f}; denominator: {denom}"})
        print(f"  [{'PASS' if lo <= v <= hi else 'FAIL'}] {item}: {v:.4f} ({lo:.0%}-{hi:.0%})")
    cma_n = con.execute("""select d.cma_code_sgc2021, d.tier, count(l.lead_id) n, count(l.lead_id) filter (where l.market_evidence_flag =
                           case when d.tier = 3 then 'scale_only_limited_evidence' else 'full_framework' end) ok
                           from wh.analytics.dim_cma d left join crm_lead l using (cma_code_sgc2021) group by 1, 2""").fetchall()
    check(G, "all 41 CMAs are represented; every CMA lead carries its tier's market-evidence flag", len(cma_n) == 41 and all(r[2] >= 20 and r[2] == r[3] for r in cma_n),
          f"min {min(r[2] for r in cma_n)} leads per CMA; Tier 3 CMAs {sum(1 for r in cma_n if r[1] == 3)}")
    geo = dict(con.execute("select geography_status, count(*) from crm_lead group by 1 order by 1").fetchall())
    check(G, "out-of-CMA, remote, unresolved, conflicting and out-of-footprint geography are all present",
          all(geo.get(k, 0) > 0 for k in ["resolved_cma", "resolved_province_remainder", "missing", "conflict", "out_of_footprint"])
          and q("select count(*) from crm_lead where territory_id = 'TER-REMOTE-NORTH'") > 0, geo)
    n = q("""select count(*) from crm_lead where (market_evidence_flag = 'no_market_context') <> (cma_code_sgc2021 is null)""")
    check(G, "leads without a CMA carry the explicit no_market_context flag and remain processable",
          n == 0 and q(f"select count(*) from crm_lead l where market_evidence_flag = 'no_market_context' and {has_rm}") > 0, n)
    bands = dict(con.execute("select employee_band, count(*) from crm_business_prospect group by 1 order by 1").fetchall())
    check(G, "all four SME size bands, plus ineligible 500-plus, are represented", all(bands.get(b, 0) > 0 for b in ["1-4", "5-19", "20-99", "100-499", "500-plus"]), bands)
    inds = dict(con.execute("select coalesce(industry_code, '(unresolved)'), count(*) from crm_business_prospect group by 1 order by 1").fetchall())
    kinds = {"mandatory specialist": mand, "preferred specialist": prefd, "unclassified": ("UNCLASSIFIED",), "excluded": ("91",)}
    check(G, "industry coverage: generalist, preferred-specialist, mandatory-specialist, unclassified and excluded industries",
          all(any(inds.get(c, 0) > 0 for c in cs) for cs in kinds.values()) and len(inds) >= 15
          and q("select count(*) from hist where status_reason = 'missing_industry'") > 0, f"{len(inds)} industry codes")
    ch = dict(con.execute("select first_channel_code, count(*) from crm_lead group by 1 order by 1").fetchall())
    check(G, "all five acquisition channels are represented", len(ch) == 5 and min(ch.values()) > 100, ch)
    caps_states = dict(con.execute("select capacity_state, count(*) from crm_rm_capacity_snapshot group by 1 order by 1").fetchall())
    avail = dict(con.execute("select availability_status, count(*) from crm_rm_capacity_snapshot group by 1 order by 1").fetchall())
    check(G, "capacity scenarios present: warnings, hard-limit states, approved exceptions, planned leave, unavailability and partial availability",
          all(caps_states.get(k, 0) > 0 for k in ["warning", "at_or_above_hard_limit", "approved_exception", "below_warning"])
          and all(avail.get(k, 0) > 0 for k in ["planned_leave", "unavailable", "partial"]),
          {**{f"state {k}": v for k, v in caps_states.items()}, **{f"availability {k}": v for k, v in avail.items()}})
    sla_o = dict(con.execute("select sla_outcome, count(*) from crm_sla_event group by 1 order by 1").fetchall())
    check(G, "SLA scenarios present: met, warning, breached, paused, exempt and escalated clocks",
          all(sla_o.get(k, 0) > 0 for k in ["met", "breached_closed", "not_applicable"]) and (sla_o.get("warning_open", 0) + sla_o.get("open", 0)) > 0
          and q("select count(*) from crm_sla_event where paused_minutes > 0") > 0 and q("select count(*) from crm_sla_event where escalation_level = 2") > 0, sla_o)
    rms = con.execute("""select count(*), count(*) filter (where rm_id in (select rm_id from crm_rm_specialization where specialization_role = 'primary')),
                         count(*) filter (where rm_id in (select rm_id from crm_rm_specialization where specialization_role = 'secondary')),
                         count(*) filter (where not active_flag), count(*) filter (where employment_start_date > date '2025-06-01') from crm_relationship_manager""").fetchone()
    check(G, "RM population: about 140 fictional RMs with generalists, primary and secondary specialists, leavers and joiners",
          120 <= rms[0] <= 160 and rms[1] > 0 and rms[2] > 0 and rms[3] > 0 and rms[4] > 0,
          f"{rms[0]} RMs; {rms[1]} dedicated specialists; {rms[2]} secondary; {rms[3]} leavers; {rms[4]} joiners")
    n = q("""select count(*) from crm_territory t where t.territory_type in ('cma', 'province_remainder') and not exists (select 1 from crm_rm_territory rt
             where rt.territory_id = t.territory_id and rt.coverage_role = 'primary')""")
    check(G, "every ordinary territory has at least one primary RM", n == 0, n)
    report = {
        "leads by channel": ch,
        "leads by size band": dict(con.execute("select p.employee_band, count(*) from crm_lead l join crm_business_prospect p using (prospect_id) group by 1 order by 1").fetchall()),
        "leads by status": dict(con.execute("select current_lead_status, count(*) from crm_lead group by 1 order by 1").fetchall()),
        "leads by market-evidence flag": dict(con.execute("select market_evidence_flag, count(*) from crm_lead group by 1 order by 1").fetchall()),
        "unassigned (queued at least once) rate": round(float(q("select avg((lead_id in (select lead_id from crm_lead_assignment where rm_id is null))::int) from crm_lead")), 4),
        "opportunity rate (valid leads)": round(float(q("select avg((lead_id in (select lead_id from crm_opportunity_outcome))::int) from crm_lead where current_lead_status not in ('INVALID','MERGED')")), 4),
        "manual-override rate (leads with an RM)": round(float(q(f"select avg((lead_id in (select lead_id from crm_lead_assignment where manual_override_flag))::int) from crm_lead l where {has_rm}")), 4),
        "territory mismatch rate (RM decisions)": round(float(q("select avg((not territory_match_flag)::int) from crm_routing_decision where selected_rm_id is not null")), 4),
        "specialist mismatch rate (specialist-need decisions)": round(float(q("select avg((not specialist_match_flag)::int) from crm_routing_decision where specialist_match_flag is not null")), 4),
        "RM utilization quantiles (10/50/90/99%)": [float(x) for x in q("select quantile_cont(utilization_ratio, [0.1, 0.5, 0.9, 0.99]) from crm_rm_capacity_snapshot where utilization_ratio is not null")],
        "capacity states": caps_states, "SLA outcomes": sla_o,
        "SLA events by rule": dict(con.execute("select sla_id, count(*) from crm_sla_event group by 1 order by 1").fetchall()),
        "leads per CMA (min/median/max)": [min(r[2] for r in cma_n), sorted(r[2] for r in cma_n)[20], max(r[2] for r in cma_n)],
    }
    for k, v in report.items():
        results[G].append({"check": f"report: {k}", "passed": True, "detail": json.dumps(v, default=str)})

    # ---------------- privacy and safety
    G = "privacy_and_safety_checks"
    print("\nPrivacy and safety")
    n = q("select count(*) from crm_lead where contact_email_fictional is not null and not regexp_full_match(contact_email_fictional, '[a-z0-9.\\-]+@[a-z0-9\\-]+\\.example')")
    check(G, "emails use reserved .example domains only", n == 0, n)
    n = q("select count(*) from crm_lead where contact_phone_fictional is not null and not regexp_full_match(contact_phone_fictional, '\\+1-\\d{3}-555-01\\d{2}')")
    check(G, "telephone numbers use the fictional 555-01xx range only", n == 0, n)
    n = q("""select count(*) from crm_business_prospect where not regexp_full_match(legal_name, '(?i)synthetic (business|operating company) \\d{6}[ a-z.]*')
             or (operating_name is not null and not regexp_full_match(operating_name, 'Synthetic Trading Name \\d{6}'))""")
    n2 = q("select count(*) from crm_campaign where not regexp_full_match(campaign_name, 'Campaign Synthetic \\d{3} \\(fictional\\)') or not regexp_full_match(campaign_code, 'CMP-FICT-.*')")
    check(G, "business and campaign names are obviously fictional placeholders", n == 0 and n2 == 0, f"{n}, {n2}")
    strings = []
    for t in tables:
        for c in fields[(fields.table_name == t) & (fields.data_type == "varchar")].column_name:
            strings += [r[0] for r in con.execute(f"select distinct {c} from {t} where {c} is not null").fetchall()]
    blob = "\n".join(strings)
    banks = sorted({b for b in REAL_BANKS if re.search(rf"\b{re.escape(b)}\b", blob)})
    check(G, "no real bank or credit-union names appear in any text value", not banks, banks or f"{len(strings)} distinct text values screened")
    pii = sorted(set(re.findall(r"\b\d{3}[- ]\d{3}[- ]\d{3}\b|\b\d{9}\s?R[TCP]\d{4}\b|\b\d{5}-\d{3}-\d{5,12}\b", blob)))
    emails = sorted(e for e in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", blob) if not e.endswith(".example"))
    check(G, "no social-insurance, business-number, account-number patterns or non-reserved emails", not pii and not emails, (pii + emails)[:5] or "none")
    ops_cols = {c for t in tables for c, _ in con.execute(f"select column_name, column_type from (describe {t})").fetchall()}
    scored = sorted(c for c in ops_cols if SCORE_RE.search(c))
    check(G, "no lead-priority score, routing score or RM recommendation field exists in the data", not scored, scored or "none")
    check(G, "ground truth is not stored with the operational entities", not (ops_cols & GROUND_TRUTH_COLUMNS) and not list(crm.glob("*truth*")), "separate folder")

    # ---------------- dbt layer (full mode)
    if not source_only:
        dbt_checks(con, tables, counts, fields)
    else:
        for g in ("dbt_source_reconciliation", "model_grain_checks"):
            results[g].append({"check": "skipped in source-only mode", "passed": True, "detail": "source-level validation of generator output"})

    # ---------------- outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in results.items():
        pd.DataFrame(rows, columns=["check", "passed", "detail"]).to_csv(out_dir / f"{name}.csv", index=False, lineterminator="\n")
    allr = [r for rows in results.values() for r in rows]
    summary = {
        "purpose": "Independent validation of the synthetic CRM data (fictional bank; synthetic records only)",
        "run_timestamp_utc": start_ts, "mode": "source-only" if source_only else "full",
        "validation_script_sha256": sha(Path(__file__)),
        "generator_version": params["generator_version"], "master_random_seed": int(params["random_seed"]),
        "period_utc": [params["period_start_utc"], params["period_end_utc"]],
        "row_counts": counts, "rates": rate_vals, "manifest_sha256": sha(man_path) if man_path.exists() else None,
        "evidence_sha256": {f"{n}.csv": sha(out_dir / f"{n}.csv") for n in results},
        "checks_by_file": {n: {"passed": sum(r["passed"] for r in rows), "total": len(rows)} for n, rows in results.items()},
        "checks_passed": sum(r["passed"] for r in allr), "checks_total": len(allr),
        "failed_checks": [r["check"] for r in allr if not r["passed"]], "all_checks_pass": all(r["passed"] for r in allr),
    }
    (out_dir / "synthetic_crm_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"\nChecks: {summary['checks_passed']} of {summary['checks_total']} pass")
    return summary


def dbt_checks(con, tables, counts, fields):
    G = "dbt_source_reconciliation"
    print("\ndbt source and staging reconciliation")
    rels = {r[0] + "." + r[1] for r in con.execute("select table_schema, table_name from information_schema.tables where table_catalog = 'wh'").fetchall()}
    for t in tables:
        stg = f"staging.stg_crm__{t[4:]}"
        if stg not in rels:
            results[G].append({"check": f"{stg} exists", "passed": False, "detail": "missing"})
            print(f"  [FAIL] {stg} missing")
            continue
        n = con.execute(f"select count(*) from wh.{stg}").fetchone()[0]
        cols = ", ".join(f.column_name for f in fields[fields.table_name == t].itertuples()
                         if not f.column_name.startswith("contact_"))
        diff = con.execute(f"select count(*) from ((select {cols} from wh.{stg} except all select {cols} from {t}) union all "
                           f"(select {cols} from {t} except all select {cols} from wh.{stg}))").fetchone()[0]
        ok = n == counts[t] and diff == 0
        results[G].append({"check": f"{stg} equals its source file", "passed": ok, "detail": f"{n} rows; {diff} differing rows"})
        print(f"  [{'PASS' if ok else 'FAIL'}] {stg}: {n} rows, {diff} differing")
    man = json.loads(DBT_MANIFEST.read_text(encoding="utf-8")) if DBT_MANIFEST.exists() else {"nodes": {}, "sources": {}}
    crm_sources = [s for s in man.get("sources", {}).values() if s.get("source_name") == "synthetic_crm"]
    ok = sorted(s["name"] for s in crm_sources) == sorted(tables) and not any("crm_ground_truth" in json.dumps(s.get("meta", {}))
                                                                           for s in man.get("sources", {}).values())
    results[G].append({"check": "dbt declares one synthetic_crm source per entity and no ground-truth source", "passed": ok, "detail": f"{len(crm_sources)} sources"})
    print(f"  [{'PASS' if ok else 'FAIL'}] dbt sources: {len(crm_sources)}")
    rr = json.loads(RUN_RESULTS.read_text(encoding="utf-8")) if RUN_RESULTS.exists() else {"results": []}
    crm_nodes = {k for k, n in man["nodes"].items() if "crm_data" in (n.get("tags") or [])}
    lr_nodes = {k for k, n in man["nodes"].items() if "lead_routing" in (n.get("tags") or [])}
    # Lead-routing tests read CRM models, so they would otherwise be counted here. Generic tests declared in the
    # lead-routing YAMLs inherit no tag, so a test is excluded when it is tagged lead_routing OR depends on a
    # lead-routing node, which is how 11 and 14 isolate the layer too.
    crm_tests = {k for k, n in man["nodes"].items() if n["resource_type"] == "test" and k not in lr_nodes
                 and not set(n.get("depends_on", {}).get("nodes", [])) & lr_nodes
                 and (k in crm_nodes or set(n.get("depends_on", {}).get("nodes", [])) & crm_nodes)}
    ran = {r["unique_id"]: r["status"] for r in rr["results"] if r["unique_id"] in crm_tests}
    ok = len(crm_tests) > 0 and len(ran) == len(crm_tests) and all(v == "pass" for v in ran.values())
    results[G].append({"check": "all CRM dbt tests passed in the latest dbt test run", "passed": ok, "detail": f"{sum(v == 'pass' for v in ran.values())} of {len(crm_tests)}"})
    print(f"  [{'PASS' if ok else 'FAIL'}] CRM dbt tests: {sum(v == 'pass' for v in ran.values())} of {len(crm_tests)}")
    G = "model_grain_checks"
    print("\nFoundational model grains")
    grains = {
        "intermediate.int_crm__lead_current_status": ("lead_id", "crm_lead"), "intermediate.int_crm__active_assignment": ("lead_id", None),
        "intermediate.int_crm__lead_assignment_history": ("assignment_id", "crm_lead_assignment"),
        "intermediate.int_crm__lead_activity_timeline": ("activity_id", "crm_lead_activity"), "intermediate.int_crm__lead_sla_history": ("sla_event_id", "crm_sla_event"),
        "intermediate.int_crm__rm_capacity_at_assignment": ("assignment_id", None), "intermediate.int_crm__duplicate_resolution": ("duplicate_group_id", "crm_duplicate_group"),
        "intermediate.int_crm__lead_market_context": ("lead_id", "crm_lead"),
        "analytics.dim_relationship_manager": ("rm_id", "crm_relationship_manager"), "analytics.dim_acquisition_channel": ("channel_code", "crm_acquisition_channel"),
        "analytics.dim_campaign": ("campaign_id", "crm_campaign"), "analytics.dim_business_prospect": ("prospect_id", "crm_business_prospect"),
        "analytics.dim_crm_territory": ("territory_id", "crm_territory"), "analytics.fct_lead": ("lead_id", "crm_lead"),
        "analytics.fct_lead_status_history": ("status_history_id", "crm_lead_status_history"), "analytics.fct_lead_assignment": ("assignment_id", "crm_lead_assignment"),
        "analytics.fct_lead_activity": ("activity_id", "crm_lead_activity"), "analytics.fct_routing_decision": ("routing_decision_id", "crm_routing_decision"),
        "analytics.fct_rm_capacity_snapshot": ("capacity_snapshot_id", "crm_rm_capacity_snapshot"), "analytics.fct_sla_event": ("sla_event_id", "crm_sla_event"),
        "analytics.fct_opportunity_outcome": ("opportunity_id", "crm_opportunity_outcome"), "analytics.fct_lead_source_touch": ("source_touch_id", "crm_lead_source_touch"),
    }
    for rel, (pk, src) in grains.items():
        if rel not in rels:
            results[G].append({"check": f"{rel} exists", "passed": False, "detail": "missing"})
            print(f"  [FAIL] {rel} missing")
            continue
        n, d = con.execute(f"select count(*), count(distinct {pk}) from wh.{rel}").fetchone()
        ok = n == d and (src is None or n == counts[src])
        results[G].append({"check": f"{rel}: one row per {pk}" + (f", one per {src} row" if src else ""), "passed": ok, "detail": f"{n} rows, {d} distinct"})
        print(f"  [{'PASS' if ok else 'FAIL'}] {rel}: {n} rows")
    n = con.execute("select count(*) from wh.intermediate.int_crm__active_assignment a join wh.analytics.fct_lead_assignment f using (assignment_id) where not f.active_flag").fetchone()[0]
    ok = n == 0 and con.execute("select count(*) from wh.intermediate.int_crm__active_assignment").fetchone()[0] == con.execute(
        "select count(distinct lead_id) from crm_lead_assignment where active_flag and assignment_role = 'primary'").fetchone()[0]
    results[G].append({"check": "int_crm__active_assignment holds exactly the active primary assignments", "passed": ok, "detail": n})
    t3 = con.execute("""select count(*) from wh.intermediate.int_crm__lead_market_context where (market_evidence_flag <> 'full_framework'
                        and (market_opportunity_score is not null or market_opportunity_rank is not null)) or (market_evidence_flag = 'full_framework'
                        and market_opportunity_score is null) or (market_evidence_flag = 'scale_only_limited_evidence' and scale_only_context_available is not true)""").fetchone()[0]
    results[G].append({"check": "Tier 3 and no-context leads keep null opportunity score and rank; Tier 3 keeps scale-only context", "passed": t3 == 0, "detail": t3})
    print(f"  [{'PASS' if t3 == 0 else 'FAIL'}] market context null handling ({t3})")
    crm_cols = {r[0] for r in con.execute("""select column_name from information_schema.columns where table_catalog = 'wh' and (table_name like '%crm%'
                or table_name in ('dim_relationship_manager', 'dim_acquisition_channel', 'dim_campaign', 'dim_business_prospect', 'fct_lead', 'fct_lead_status_history',
                'fct_lead_assignment', 'fct_lead_activity', 'fct_routing_decision', 'fct_rm_capacity_snapshot', 'fct_sla_event', 'fct_opportunity_outcome',
                'fct_lead_source_touch'))""").fetchall()}
    bad = sorted((crm_cols & GROUND_TRUTH_COLUMNS) | {c for c in crm_cols if SCORE_RE.search(c)} | {c for c in crm_cols if c.startswith("contact_email") or c.startswith("contact_phone")} - {
        c for c in crm_cols if False})
    mart_contact = {r[0] for r in con.execute("""select column_name from information_schema.columns where table_catalog = 'wh' and table_schema = 'analytics'
                    and (column_name like 'contact_email%' or column_name like 'contact_phone%')""").fetchall()}
    bad = sorted(((crm_cols & GROUND_TRUTH_COLUMNS) | {c for c in crm_cols if SCORE_RE.search(c)}) | mart_contact)
    results[G].append({"check": "CRM models contain no ground-truth, score or recommendation columns; fictional contact fields stay out of marts", "passed": not bad,
                       "detail": bad or "clean"})
    print(f"  [{'PASS' if not bad else 'FAIL'}] no leakage, scores or contact fields in marts ({bad or 'clean'})")
    ds = json.loads(DESIGN_SUMMARY.read_text(encoding="utf-8")) if DESIGN_SUMMARY.exists() else {}
    changed = [p for p, h in ds.get("catalog_sha256", {}).items() if not (ROOT / p).exists() or sha(ROOT / p) != h]
    ok = bool(ds.get("all_checks_pass")) and not changed
    results[G].append({"check": "upstream: CRM design validation passed and the design catalogs are unchanged since it ran", "passed": ok,
                       "detail": changed or f"{len(ds.get('catalog_sha256', {}))} catalogs"})
    print(f"  [{'PASS' if ok else 'FAIL'}] design catalogs unchanged since design validation")


# ------------------------------------------------------------------ mutation tests
MUTATIONS = [
    ("duplicate primary key", "crm_lead", "select * from t union all (select * from t order by lead_id limit 1)", "primary keys are unique and not null"),
    ("broken foreign key", "crm_lead_activity", "select * replace (case when activity_id = (select min(activity_id) from t) then 'ASG-9999999' else assignment_id end as assignment_id) from t",
     "all foreign keys resolve"),
    ("invalid status transition", "crm_lead_status_history",
     "select * replace (case when status_history_id = (select min(status_history_id) from t where to_status = 'CONVERTED') then 'CONTACTED' else from_status end as from_status) from t",
     "every status change uses an allowed transition"),
    ("impossible event timestamp", "crm_lead_activity",
     "select * replace (case when activity_id = (select min(activity_id) from t) then activity_at - interval 30 days else activity_at end as activity_at) from t",
     "activities occur while their assignment is active"),
    ("multiple active assignments", "crm_lead_assignment",
     "select * from t union all (select * replace ('ASG-9999998' as assignment_id) from t where active_flag and rm_id is not null order by assignment_id limit 1)",
     "at most one active primary assignment per lead"),
    ("missing override reason", "crm_lead_assignment",
     "select * replace (case when manual_override_flag then null else override_reason_code end as override_reason_code) from t",
     "manual overrides have a reason code and actor role"),
    ("real-looking email domain", "crm_lead",
     "select * replace (case when lead_id = (select min(lead_id) from t) then 'someone@gmail.com' else contact_email_fictional end as contact_email_fictional) from t",
     "emails use reserved .example domains only"),
    ("leaked ground-truth field", "crm_lead", "select *, 'SC-01' as primary_scenario_id from t", "operational tables contain only dictionary columns and no ground-truth fields"),
    ("missing required scenario", "crm_lead", "select * replace ('not stated' as stated_service_language) from t", "all 33 approved scenarios are observable"),
    ("out-of-range conversion rate", "crm_lead",
     "select * replace (case when current_lead_status = 'CLOSED_LOST' and hash(lead_id) % 3 = 0 then 'CONVERTED' else current_lead_status end as current_lead_status) from t",
     "lead-to-conversion rate within"),
]


def mutation_tests(data_root, out_dir):
    print("\nMutation tests (temporary copies only)")
    base = ROOT / "target" / "synthetic_crm_mutation"
    rows = []
    published = {p.relative_to(data_root).as_posix(): sha(p) for p in sorted(data_root.rglob("*")) if p.is_file()}
    for name, table, sql, target in MUTATIONS:
        if base.exists():
            shutil.rmtree(base)
        shutil.copytree(data_root / "crm", base / "crm")
        shutil.copytree(data_root / "crm_ground_truth", base / "crm_ground_truth")
        f = base / "crm" / f"{table}.parquet"
        con = duckdb.connect()
        con.execute(f"create table t as select * from read_parquet('{f.as_posix()}')")
        con.execute(f"copy ({sql}) to '{f.as_posix()}' (format parquet)")
        con.close()
        ev = base / "evidence"
        r = subprocess.run([sys.executable, str(Path(__file__)), "--source-only", "--data-root", str(base), "--output-dir", str(ev)],
                           capture_output=True, text=True)
        summ = json.loads((ev / "synthetic_crm_summary.json").read_text(encoding="utf-8")) if (ev / "synthetic_crm_summary.json").exists() else {}
        hit = [c for c in summ.get("failed_checks", []) if c.startswith(target)]
        ok = r.returncode == 1 and bool(hit)
        rows.append({"mutation": name, "table": table, "target_check": target, "validator_exit_code": r.returncode, "target_check_failed": bool(hit),
                     "detected": ok})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: exit {r.returncode}, target check failed: {bool(hit)}")
    shutil.rmtree(base, ignore_errors=True)
    after = {p.relative_to(data_root).as_posix(): sha(p) for p in sorted(data_root.rglob("*")) if p.is_file()}
    restored = after == published
    rows.append({"mutation": "published data unchanged after mutation tests", "table": "all", "target_check": "sha256 of every published file",
                 "validator_exit_code": "", "target_check_failed": "", "detected": restored})
    print(f"  [{'PASS' if restored else 'FAIL'}] published data unchanged ({len(after)} files)")
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out_dir / "mutation_test_results.csv", index=False, lineterminator="\n")
    return all(r["detected"] for r in rows)


def main():
    import os
    os.chdir(ROOT)  # dbt sources and staging views use repository-relative paths
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA))
    ap.add_argument("--output-dir")
    ap.add_argument("--mutation-tests", action="store_true")
    a = ap.parse_args()
    data_root = Path(a.data_root)
    out_dir = Path(a.output_dir) if a.output_dir else (DEFAULT_OUT if data_root.resolve() == DEFAULT_DATA.resolve() else data_root / "validation")
    if a.mutation_tests:
        ok = mutation_tests(data_root, out_dir)
        print("MUTATION TESTS " + ("PASSED" if ok else "FAILED"))
        sys.exit(0 if ok else 1)
    s = validate(data_root, out_dir, a.source_only)
    print("SYNTHETIC CRM VALIDATION " + ("PASSED" if s["all_checks_pass"] else "FAILED"))
    sys.exit(0 if s["all_checks_pass"] else 1)


if __name__ == "__main__":
    main()
