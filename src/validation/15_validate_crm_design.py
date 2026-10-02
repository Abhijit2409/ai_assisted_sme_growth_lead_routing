"""
Validation of the hypothetical CRM acquisition-process design.

The design is a set of reviewed catalogs in data/design/crm/ plus two documents:
docs/crm_acquisition_process_and_requirements.md and docs/crm_data_model_and_dictionary.md.
Nothing here describes a real bank; no CRM records exist yet. This script checks that:
- the market opportunity framework validation passed (precondition);
- every required catalog exists, has its columns, unique identifiers and complete mandatory fields;
- the lead lifecycle is a valid state machine (reachability, terminal handling, dispositions, audited
  reopening, named invalid transitions blocked) and the assignment lifecycle is consistent;
- every identifier referenced anywhere in the catalogs resolves, SLA events exist, CMA and industry keys
  exist in analytics.dim_cma and analytics.dim_industry, analytics tables and columns named as sources exist;
- every Must requirement traces to a process step, entity, acceptance criterion with a future test, and scenario;
- all 32 required synthetic scenarios are covered and their status paths use only valid transitions;
- the documents carry the hypothetical-design disclaimer, the six diagrams, the state machine and ERD that
  match the catalogs, and every documented count reproduced from the catalogs;
- design changes made for the synthetic implementation are logged and match the catalogs;
- CRM records exist only as manifest-listed synthetic data under data/synthetic/crm, CRM dbt files sit in the
  crm_data-tagged paths, CRM warehouse relations are crm_data dbt models, and no priority score, routing score
  or RM recommendation exists; no personal data, real-bank names or internal project phase labels appear in
  the design artifacts.

Writes data/validation/crm_design/{design_completeness_checks, transition_validation,
rule_reference_validation, requirements_coverage, scenario_coverage, documentation_checks}.csv and
crm_design_summary.json. Deterministic except run_timestamp_utc. Exits with status 1 if any check fails.

    C:/Users/abhij/anaconda3/python.exe src/validation/15_validate_crm_design.py [--preconditions-only] [--print-claims]
"""

from pathlib import Path
import datetime as dt
import hashlib
import json
import re
import sys
from collections import deque

import duckdb
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "design" / "crm"
V = ROOT / "data" / "validation" / "crm_design"
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
OPP_SUMMARY = ROOT / "data" / "validation" / "opportunity_framework" / "opportunity_framework_summary.json"
DOC_PROCESS = ROOT / "docs" / "crm_acquisition_process_and_requirements.md"
DOC_MODEL = ROOT / "docs" / "crm_data_model_and_dictionary.md"
README = ROOT / "README.md"
DBT_MANIFEST = ROOT / "target" / "manifest.json"
SYNTH_MANIFEST = ROOT / "data" / "synthetic" / "crm" / "synthetic_crm_manifest.csv"
CRM_DBT_PATHS = ("models/staging/crm/", "models/intermediate/crm/", "models/marts/crm/", "models/sources/_crm_sources.yml", "tests/crm/")
SCORE_RE = re.compile(r"priority_score|routing_score|lead_score|recommended_rm|rm_recommendation|assignment_optimi|propensity")
DISCLAIMER = "hypothetical design for a fictional bank"

REQUIRED = {
    "assumption_register.csv": ["assumption_id", "category", "statement", "rationale", "approval_needed_from", "approval_status", "hypothetical_flag"],
    "stakeholder_needs.csv": ["need_id", "stakeholder", "stakeholder_role", "need", "related_process_steps", "related_requirements"],
    "current_state_problem_catalog.csv": ["problem_id", "problem_title", "hypothetical_scenario", "consequence", "target_state_control", "hypothetical_flag"],
    "process_step_catalog.csv": ["step_id", "step_order", "step_name", "description", "lead_status_after", "owner_role", "rule_references", "related_requirements"],
    "acquisition_channel_catalog.csv": ["channel_code", "channel_name", "intake_method", "required_fields", "expected_validation", "ownership_start",
                                        "assignment_timing", "first_contact_expectation", "duplicate_handling", "attribution_rule", "common_exceptions"],
    "lead_status_catalog.csv": ["status_code", "status_name", "definition", "entry_event", "exit_condition", "is_terminal", "required_timestamp",
                                "requires_disposition", "owner_role", "applicable_sla_ids", "reporting_treatment"],
    "lead_disposition_catalog.csv": ["disposition_code", "disposition_name", "applies_to_status", "description", "reporting_group"],
    "crm_event_catalog.csv": ["event_code", "event_name", "description", "source_entity"],
    "lead_transition_rules.csv": ["transition_id", "from_status", "to_status", "trigger_event", "transition_type", "guard_condition", "required_fields",
                                  "actor_role", "requires_disposition", "audit_required"],
    "invalid_transition_examples.csv": ["example_id", "from_status", "to_status", "why_invalid", "preventing_control", "violation_type"],
    "assignment_status_catalog.csv": ["status_code", "status_name", "definition", "is_active", "is_terminal", "allowed_next_statuses", "required_timestamp", "owner_role"],
    "routing_rule_catalog.csv": ["rule_id", "rule_category", "rule_name", "business_purpose", "decision_order", "inputs", "condition", "outcome",
                                 "exception_handling", "fallback", "rule_owner", "audit_fields", "configurable_parameters", "acceptance_criterion",
                                 "rule_version", "effective_from", "hypothetical_flag"],
    "territory_rule_catalog.csv": ["rule_id", "rule_name", "territory_type", "condition", "outcome", "fallback", "geographic_key", "rule_owner",
                                   "configurable_parameters", "rule_version", "effective_from", "hypothetical_flag"],
    "specialist_rule_catalog.csv": ["rule_id", "industry_system", "industry_code", "industry_label", "specialist_type", "requirement_level", "rule_text",
                                    "fallback", "rule_owner", "rule_version", "effective_from", "hypothetical_flag"],
    "rm_capacity_rule_catalog.csv": ["rule_id", "component", "weight", "unit", "threshold_type", "threshold", "warning_level", "hard_limit",
                                     "exception_handling", "review_frequency", "rule_owner", "rule_version", "effective_from", "hypothetical_flag"],
    "sla_rule_catalog.csv": ["sla_id", "sla_name", "start_event", "stop_events", "target_value", "target_unit", "time_basis", "warning_pct", "breach_pct",
                             "applies_to", "responsible_role", "escalation_level_1", "escalation_level_2", "reassignment_behavior", "pause_conditions",
                             "exception_conditions", "configurable", "rule_version", "effective_from", "hypothetical_flag"],
    "duplicate_rule_catalog.csv": ["rule_id", "match_tier", "rule_name", "inputs", "condition", "outcome", "confidence", "notes", "rule_version",
                                   "effective_from", "hypothetical_flag"],
    "crm_entity_catalog.csv": ["entity_id", "table_name", "business_purpose", "grain", "primary_key", "natural_key", "foreign_keys", "record_type",
                               "effective_dates", "retention", "relationships", "future_dbt_model", "field_count"],
    "crm_field_dictionary.csv": ["table_name", "column_name", "ordinal_position", "data_type", "nullable", "accepted_values", "description",
                                 "fictional_example", "privacy_class", "validation_rule", "field_category", "foreign_key", "is_primary_key"],
    "kpi_definition_catalog.csv": ["kpi_id", "kpi_family", "kpi_name", "business_purpose", "numerator", "denominator", "grain", "relevant_date", "filters",
                                   "exclusions", "dimensions", "owner", "refresh_frequency", "desired_direction", "future_mart", "limitations", "target"],
    "business_requirements.csv": ["requirement_id", "requirement_type", "title", "statement", "rationale", "stakeholders", "moscow_priority",
                                  "source_assumption_id", "acceptance_criterion_ids"],
    "acceptance_criteria.csv": ["acceptance_criterion_id", "primary_requirement_id", "criterion", "measurement", "future_test_name", "test_type", "all_requirement_ids"],
    "requirements_traceability.csv": ["requirement_id", "moscow_priority", "process_step_id", "entity", "rule_or_transition_id", "kpi_id",
                                      "acceptance_criterion_ids", "scenario_ids", "future_test_names"],
    "synthetic_scenario_catalog.csv": ["scenario_id", "scenario_name", "purpose", "required_condition", "entities_involved", "setup", "expected_status_path",
                                       "transition_ids", "expected_assignment_behavior", "expected_sla_result", "expected_final_outcome", "rule_ids",
                                       "kpi_ids", "acceptance_criterion_ids", "indicative_share_pct_range", "exercises_exception_flag"],
    "synthetic_volume_specification.csv": ["item", "recommended_value", "range_low", "range_high", "unit", "basis", "synthetic_assumption_flag"],
    "lead_priority_input_catalog.csv": ["input_id", "input_family", "input_name", "source", "may_affect_priority", "may_affect_routing_eligibility",
                                        "may_affect_sla", "context_only", "never_use", "note"],
    "business_calendar.csv": ["calendar_entry_id", "entry_type", "calendar_date", "province_codes", "description", "value", "calendar_version", "approval_status"],
    "synthetic_generation_parameters.csv": ["parameter_id", "parameter_group", "parameter_name", "value", "unit", "rationale", "source_reference", "approval_status"],
    "design_change_log.csv": ["change_id", "change_date", "catalog", "object_id", "change_type", "before", "after", "reason", "related_reference"],
}
KEYS = {"assumption_register.csv": "assumption_id", "stakeholder_needs.csv": "need_id", "current_state_problem_catalog.csv": "problem_id",
        "process_step_catalog.csv": "step_id", "acquisition_channel_catalog.csv": "channel_code", "lead_status_catalog.csv": "status_code",
        "lead_disposition_catalog.csv": "disposition_code", "crm_event_catalog.csv": "event_code", "lead_transition_rules.csv": "transition_id",
        "invalid_transition_examples.csv": "example_id", "assignment_status_catalog.csv": "status_code", "routing_rule_catalog.csv": "rule_id",
        "territory_rule_catalog.csv": "rule_id", "specialist_rule_catalog.csv": "rule_id", "rm_capacity_rule_catalog.csv": "rule_id",
        "sla_rule_catalog.csv": "sla_id", "duplicate_rule_catalog.csv": "rule_id", "crm_entity_catalog.csv": "table_name",
        "kpi_definition_catalog.csv": "kpi_id", "business_requirements.csv": "requirement_id", "acceptance_criteria.csv": "acceptance_criterion_id",
        "requirements_traceability.csv": "requirement_id", "synthetic_scenario_catalog.csv": "scenario_id",
        "synthetic_volume_specification.csv": "item", "lead_priority_input_catalog.csv": "input_id",
        "business_calendar.csv": "calendar_entry_id", "synthetic_generation_parameters.csv": "parameter_id", "design_change_log.csv": "change_id"}
REQUIRED_LEAD_STATUSES = ["NEW", "VALIDATION_PENDING", "DUPLICATE_REVIEW", "READY_FOR_ASSIGNMENT", "ASSIGNED", "CONTACTED", "NURTURING",
                          "QUALIFIED", "CONVERTED", "CLOSED_LOST", "INVALID", "MERGED", "WITHDRAWN"]
REQUIRED_TERMINAL = {"CONVERTED", "CLOSED_LOST", "INVALID", "MERGED", "WITHDRAWN"}
NAMED_INVALID = [("CLOSED_LOST", "ASSIGNED"), ("CONTACTED", "CONVERTED"), ("NEW", "ASSIGNED"), ("READY_FOR_ASSIGNMENT", "CONTACTED"),
                 ("NEW", "CONVERTED"), ("MERGED", "READY_FOR_ASSIGNMENT"), ("INVALID", "READY_FOR_ASSIGNMENT")]
ROUTING_CATEGORIES = ["eligibility", "exclusion", "preference", "load_balancing", "fallback", "override"]
KPI_FAMILIES = ["outcome", "flow", "speed", "routing and capacity", "data quality"]
REQ_PREFIXES = ["BR", "FR", "DR", "DQ", "RR", "NFR"]
MOSCOW = {"Must", "Should", "Could", "Won't for current scope"}
CHANNELS = ["CH-DIG", "CH-BRN", "CH-MKT", "CH-PTR", "CH-RMP"]
ASSIGNMENT_PRESERVED = ["assignment_id", "lead_id", "rm_id", "queue_code", "assignment_role", "assignment_status", "active_flag", "assigned_at",
                        "assignment_reason", "routing_decision_id", "rule_version_id", "prior_assignment_id", "prior_rm_id", "acknowledged_at",
                        "reassignment_reason", "ended_at", "manual_override_flag", "override_reason_code", "override_user_role"]
REQUIRED_SCENARIO_CONDITIONS = [
    "clean digital lead assigned successfully", "branch referral", "marketing-campaign response", "partner referral", "RM-prospected lead",
    "incomplete lead requiring validation", "invalid or ineligible lead", "exact duplicate", "probable duplicate requiring review", "unassigned lead",
    "overloaded RM", "RM temporarily unavailable", "territory mismatch", "industry-specialist mismatch",
    "preferred-language handling based on an explicit preference", "assignment acknowledgement delay", "first-contact SLA breach",
    "repeated failed contact attempts", "inactivity escalation", "automatic reassignment", "manager-approved manual override", "lead nurturing",
    "qualified opportunity", "successful conversion", "closed-lost lead", "lead withdrawn", "out-of-CMA lead", "Tier 3 market lead",
    "missing market-opportunity context", "capacity-boundary case", "multiple historical assignments with one active assignment",
    "channel attribution preserved after duplicate merge"]
ID_RE = re.compile(r"\b(?:RT-[EPXLFO]\d{2}|TER-\d{2}|SPC-\d{2}|CAP-\d{2}|SLA-\d{2}[A-D]?|DUP-\d{2}|T-\d{2}|PS-\d{2}|KPI-[OFSRD]\d{2}|AC-\d{3}"
                   r"|SC-\d{2}|A-\d{3}|CH-[A-Z]{3}|DSP-[A-Z]+(?:-[A-Z]+)*|PI-\d{2}|CSP-\d{2}|SN-\d{3}|(?:BR|FR|DR|DQ|RR|NFR)-\d{3}|IT-\d{2}|E-\d{2})\b")
ANALYTICS_RE = re.compile(r"\banalytics\.([a-z_][a-z0-9_]*)(?:\.([a-z_][a-z0-9_]*))?")
# Word-boundary names of Canadian banks and credit unions; the design must never name a real institution.
REAL_BANKS = ["Royal Bank", "RBC", "Toronto-Dominion", "TD Bank", "TD Canada", "Bank of Montreal", "BMO", "Scotiabank", "Bank of Nova Scotia",
              "CIBC", "Canadian Imperial", "National Bank", "Desjardins", "HSBC", "Laurentian", "ATB Financial", "EQ Bank", "Tangerine",
              "Simplii", "Vancity", "Servus", "Meridian", "Coast Capital", "Manulife Bank", "Canadian Western Bank"]
PHASE_RE = re.compile(r"\b[Pp]hase[ _-]?[A-H]\b|\bphase_[a-h]_|\bPhase\s+\d+\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
SIN_RE = re.compile(r"\b\d{3}[- ]\d{3}[- ]\d{3}\b")
BN_RE = re.compile(r"\b\d{9}\s?R[TCP]\d{4}\b")
ACCOUNT_RE = re.compile(r"\b\d{5}-\d{3}-\d{5,12}\b")
EXPECTED_DIAGRAMS = {DOC_PROCESS: ["hypothetical current state", "target-state acquisition process", "lead status state machine",
                                   "routing decision flow", "sla escalation and reassignment"],
                     DOC_MODEL: ["logical data model"]}

results = {k: [] for k in ["design_completeness_checks", "transition_validation", "rule_reference_validation", "requirements_coverage",
                           "scenario_coverage", "documentation_checks"]}


def check(group, name, ok, detail=""):
    results[group].append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def split(v):
    return [x.strip() for x in str(v).split(";") if x.strip() and x.strip().lower() not in ("none", "nan")]


def truthy(v):
    return str(v).strip().lower() == "true"


def preconditions():
    ok = True
    s = json.loads(OPP_SUMMARY.read_text(encoding="utf-8")) if OPP_SUMMARY.exists() else {}
    opp_ok = bool(s.get("all_checks_pass")) and s.get("checks_passed") == s.get("checks_total") and not s.get("failed_checks")
    print(f"  [{'PASS' if opp_ok else 'FAIL'}] market opportunity framework validation passed "
          f"({s.get('checks_passed')} of {s.get('checks_total')}; run {s.get('run_timestamp_utc')})")
    ok &= opp_ok
    missing = [f for f in REQUIRED if not (D / f).exists()]
    print(f"  [{'PASS' if not missing else 'FAIL'}] design catalogs present in data/design/crm ({len(REQUIRED) - len(missing)} of {len(REQUIRED)})"
          + (f"; missing {missing}" if missing else ""))
    ok &= not missing
    print(f"  [{'PASS' if WAREHOUSE.exists() else 'FAIL'}] analytical warehouse present (read-only reference for CMA and industry keys)")
    ok &= WAREHOUSE.exists()
    return ok, s


def main():
    start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    print("CRM design preconditions")
    pre_ok, opp = preconditions()
    if "--preconditions-only" in sys.argv:
        print("CRM DESIGN PRECONDITIONS " + ("PASSED" if pre_ok else "FAILED"))
        sys.exit(0 if pre_ok else 1)
    if not pre_ok:
        print("CRM DESIGN VALIDATION FAILED (preconditions)")
        sys.exit(1)

    C = {f: pd.read_csv(D / f, dtype=str, keep_default_na=False) for f in REQUIRED}
    st, tr, disp, ev = C["lead_status_catalog.csv"], C["lead_transition_rules.csv"], C["lead_disposition_catalog.csv"], C["crm_event_catalog.csv"]
    asg, rt, ter, spc = C["assignment_status_catalog.csv"], C["routing_rule_catalog.csv"], C["territory_rule_catalog.csv"], C["specialist_rule_catalog.csv"]
    cap, sla, dup = C["rm_capacity_rule_catalog.csv"], C["sla_rule_catalog.csv"], C["duplicate_rule_catalog.csv"]
    ent, fld, kpi = C["crm_entity_catalog.csv"], C["crm_field_dictionary.csv"], C["kpi_definition_catalog.csv"]
    req, acc, trc = C["business_requirements.csv"], C["acceptance_criteria.csv"], C["requirements_traceability.csv"]
    scn, vol, pri = C["synthetic_scenario_catalog.csv"], C["synthetic_volume_specification.csv"], C["lead_priority_input_catalog.csv"]
    ps, ch, csp, asm = C["process_step_catalog.csv"], C["acquisition_channel_catalog.csv"], C["current_state_problem_catalog.csv"], C["assumption_register.csv"]

    # ------------------------------------------------------------------ completeness
    G = "design_completeness_checks"
    print("\nDesign completeness")
    bad_cols = {f: [c for c in cols if c not in C[f].columns] for f, cols in REQUIRED.items()}
    bad_cols = {f: c for f, c in bad_cols.items() if c}
    check(G, "every required catalog has its required columns", not bad_cols, f"{len(REQUIRED)} catalogs" + (f"; {bad_cols}" if bad_cols else ""))
    dups = {f: int(C[f][k].duplicated().sum()) for f, k in KEYS.items() if C[f][k].duplicated().any()}
    check(G, "catalog identifiers are unique", not dups, dups or f"{len(KEYS)} keyed catalogs")
    empty_keys = {f: int((C[f][k] == "").sum()) for f, k in KEYS.items() if (C[f][k] == "").any()}
    check(G, "no blank identifiers", not empty_keys, empty_keys or "none blank")
    fmt = []
    for f in sorted(D.glob("*.csv")):
        raw = f.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or b"\r\n" in raw or not raw.endswith(b"\n"):
            fmt.append(f.name)
    check(G, "catalogs are UTF-8 without BOM, LF line endings", not fmt, fmt or f"{len(list(D.glob('*.csv')))} files")
    check(G, "20 target-state process steps in order", list(ps.step_order.astype(int)) == list(range(1, 21)) and len(ps) == 20, f"{len(ps)} steps")
    check(G, "13 hypothetical current-state problems", len(csp) == 13, len(csp))
    check(G, "five acquisition channels with every field populated",
          sorted(ch.channel_code) == sorted(CHANNELS) and not (ch[REQUIRED["acquisition_channel_catalog.csv"]] == "").any().any(), ", ".join(ch.channel_code))
    missing_st = [s for s in REQUIRED_LEAD_STATUSES if s not in set(st.status_code)]
    check(G, "lead status catalog contains the minimum status set", not missing_st, f"{len(st)} statuses" + (f"; missing {missing_st}" if missing_st else ""))
    check(G, "separate lead, assignment, activity, SLA and opportunity status domains",
          {"current_lead_status", "assignment_status", "activity_outcome", "sla_outcome", "opportunity_status"} <= set(fld.column_name)
          and not set(st.status_code) & {"ACKNOWLEDGED", "QUEUED", "BREACHED", "MET"}, "five status fields in the data model")
    rcols = REQUIRED["routing_rule_catalog.csv"]
    blank_rt = rt[(rt[rcols] == "").any(axis=1)].rule_id.tolist()
    check(G, "routing rules cover all six categories with every field populated",
          set(ROUTING_CATEGORIES) == set(rt.rule_category) and not blank_rt, f"{rt.rule_category.value_counts().to_dict()}" + (f"; blank {blank_rt}" if blank_rt else ""))
    orders = sorted(rt.decision_order.astype(int))
    check(G, "routing decision order is contiguous and unique", orders == list(range(1, len(rt) + 1)), f"1..{len(rt)}")
    rt_text = " ".join(rt[["inputs", "condition", "outcome", "configurable_parameters"]].astype(str).agg(" ".join, axis=1)).lower()
    check(G, "routing rules contain no priority score, market score or scoring weights",
          "score" not in rt_text and "opportunity" not in rt_text and not re.search(r"\bweights?\b(?<!workload_weights)", rt_text.replace("workload_weights", "")),
          "only workload weights (capacity) are configurable")
    check(G, "SLA catalog: every SLA configurable with positive target, warning below breach, responsible role and escalation",
          all(float(r.target_value) > 0 and float(r.warning_pct) < float(r.breach_pct) and r.responsible_role and r.escalation_level_1
              and truthy(r.configurable) for r in sla.itertuples()), f"{len(sla)} SLAs")
    capd = cap.set_index("rule_id")
    warn, hard, exc = float(capd.loc["CAP-06", "warning_level"]), float(capd.loc["CAP-06", "hard_limit"]), float(capd.loc["CAP-09", "hard_limit"])
    weights = cap[cap.threshold_type == "weighted_sum"].weight.astype(float)
    check(G, "capacity rules: positive weights, warning < hard limit < exception ceiling, positive base capacity",
          (weights > 0).all() and warn < hard < exc and float(capd.loc["CAP-05", "threshold"]) > 0,
          f"warning {warn}, hard {hard}, exception {exc}, base {capd.loc['CAP-05', 'threshold']} points")
    tiers = set(dup.match_tier)
    auto_ok = set(dup[dup.outcome == "auto_merge"].match_tier) <= {"exact", "high_confidence"} and set(dup[dup.match_tier == "review"].outcome) == {"review"}
    check(G, "duplicate rules are tiered (exact, high-confidence, review, merge behaviour, non-match); only exact and high-confidence auto-merge",
          tiers == {"exact", "high_confidence", "review", "merge_behavior", "non_match"} and auto_ok, sorted(tiers))
    fcounts = fld.groupby("table_name").size()
    check(G, "at least 16 entities; every entity has a field contract whose count matches the entity catalog",
          len(ent) >= 16 and all(int(r.field_count) == int(fcounts.get(r.table_name, 0)) for r in ent.itertuples()), f"{len(ent)} entities, {len(fld)} fields")
    pk_ok = all(((fld.table_name == r.table_name) & (fld.column_name == r.primary_key) & (fld.is_primary_key == "true")).any() for r in ent.itertuples())
    check(G, "every entity primary key is defined in the field dictionary", pk_ok and (fld.is_primary_key == "true").sum() == len(ent))
    audit_ok = all({"created_at", "updated_at", "record_source"} <= set(fld[fld.table_name == t].column_name) for t in ent.table_name)
    check(G, "every entity carries the audit columns created_at, updated_at, record_source", audit_ok)
    fcols = ["data_type", "nullable", "description", "fictional_example", "privacy_class", "validation_rule", "field_category"]
    blank_f = fld[(fld[fcols] == "").any(axis=1)]
    check(G, "every field has type, nullability, description, fictional example, privacy class, validation and category",
          blank_f.empty, "complete" if blank_f.empty else blank_f[["table_name", "column_name"]].values.tolist()[:5])
    missing_pres = [c for c in ASSIGNMENT_PRESERVED if c not in set(fld[fld.table_name == "crm_lead_assignment"].column_name)]
    check(G, "assignment history preserves prior owner, reasons, override, rule version and timestamps", not missing_pres, missing_pres or f"{len(ASSIGNMENT_PRESERVED)} fields")
    fam = kpi.kpi_family.value_counts().to_dict()
    kcols = REQUIRED["kpi_definition_catalog.csv"]
    check(G, "KPI tree covers the five families with every definition field populated",
          set(KPI_FAMILIES) == set(kpi.kpi_family) and not (kpi[kcols] == "").any().any(), fam)
    check(G, "KPI targets are not presented as production targets",
          all("no production target" in t for t in kpi.target), "baseline to be measured on synthetic data")
    pref = req.requirement_id.str.extract(r"^([A-Z]+)-")[0]
    check(G, "requirements use BR, FR, DR, DQ, RR and NFR prefixes with MoSCoW priorities",
          set(pref) == set(REQ_PREFIXES) and set(req.moscow_priority) <= MOSCOW, f"{pref.value_counts().to_dict()}; {req.moscow_priority.value_counts().to_dict()}")
    check(G, "every acceptance criterion is measurable and names a future test",
          not (acc[["criterion", "measurement", "future_test_name", "test_type"]] == "").any().any() and acc.future_test_name.is_unique, f"{len(acc)} criteria")
    vnum = vol[vol.range_low != ""]
    def num(x):
        return float(str(x).rstrip("%"))
    check(G, "volume specification: recommended value within its range for every ranged item",
          all(num(r.range_low) <= num(r.recommended_value) <= num(r.range_high) for r in vnum.itertuples()), f"{len(vnum)} ranged items")
    rates = vol[vol.unit.str.startswith("share of")]
    check(G, "synthetic rates are labelled as design assumptions, not benchmarks",
          len(rates) >= 5 and all("not a benchmark" in b for b in rates.basis) and all(truthy(x) for x in vol.synthetic_assumption_flag), f"{len(rates)} rates")
    hyp_cols = [f for f in REQUIRED if "hypothetical_flag" in REQUIRED[f]]
    check(G, "rule catalogs, current-state problems and assumptions are flagged hypothetical",
          all(C[f].hypothetical_flag.map(truthy).all() for f in hyp_cols), f"{len(hyp_cols)} catalogs")
    check(G, "every current-state problem is worded as a hypothetical scenario or illustrative control gap",
          all(s.startswith(("Hypothetical current-state scenario", "Illustrative control gap")) for s in csp.hypothetical_scenario))
    check(G, "SLA durations, capacity and specialist coverage are marked as requiring approval",
          set(asm[asm.approval_status == "requires approval"].category) >= {"SLA", "capacity", "specialists", "time"})

    # scope and privacy
    entity_names = set(ent.table_name)
    # CRM records may exist only as published synthetic data (data/synthetic/crm, listed in its manifest)
    data_files = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "data").rglob("*") if p.is_file() and p.stem in entity_names)
    listed = set()
    if SYNTH_MANIFEST.exists():
        listed = {f"data/synthetic/crm/{n}" for n in pd.read_csv(SYNTH_MANIFEST, dtype=str, keep_default_na=False).file_name}
    stray = [f for f in data_files if not (f.startswith("data/synthetic/crm/") and f in listed)]
    check(G, "CRM record files exist only as synthetic data under data/synthetic/crm and are listed in its manifest", not stray,
          stray or f"{len(data_files)} synthetic entity files")
    crm_files = sorted(p.relative_to(ROOT).as_posix() for base in ("models", "seeds", "tests", "snapshots", "macros")
                       for p in (ROOT / base).rglob("*") if p.is_file() and "crm" in p.as_posix().lower())
    outside = [f for f in crm_files if not f.startswith(CRM_DBT_PATHS)]
    untagged = [f for f in crm_files if f.startswith("tests/crm/") and "crm_data" not in (ROOT / f).read_text(encoding="utf-8")]
    # models may not define score or recommendation fields (tests may name them, because they check for their absence)
    scored = [f for f in crm_files if f.startswith("models/") and SCORE_RE.search((ROOT / f).read_text(encoding="utf-8").lower())]
    project = (ROOT / "dbt_project.yml").read_text(encoding="utf-8")
    tag_ok = not crm_files or project.count('+tags: ["crm_data"]') >= 4
    check(G, "CRM dbt files sit in the tagged crm_data paths and define no priority score, routing score or RM recommendation",
          not outside and not untagged and not scored and tag_ok, (outside + untagged + scored) or f"{len(crm_files)} files")
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        wh_crm = [r[0] for r in con.execute("select table_schema || '.' || table_name from information_schema.tables "
                                           "where lower(table_name) like '%crm%'").fetchall()]
        tables = {r[0]: set() for r in con.execute("select table_name from information_schema.tables where table_schema = 'analytics'").fetchall()}
        for t, c in con.execute("select table_name, column_name from information_schema.columns where table_schema = 'analytics'").fetchall():
            tables[t].add(c)
        dim_cma = con.execute("select * from analytics.dim_cma").df()
        dim_ind = con.execute("select * from analytics.dim_industry").df()
    crm_models = set()
    if DBT_MANIFEST.exists():
        crm_models = {n["name"] for n in json.loads(DBT_MANIFEST.read_text(encoding="utf-8"))["nodes"].values()
                      if n["resource_type"] == "model" and "crm_data" in (n.get("tags") or [])}
    unknown_rel = [r for r in wh_crm if r.split(".")[1] not in crm_models]
    check(G, "CRM warehouse relations are all crm_data-tagged dbt models", not unknown_rel, unknown_rel or f"{len(wh_crm)} relations")
    misplaced = [p.relative_to(ROOT).as_posix() for base in ("data/raw", "seeds") for p in (ROOT / base).rglob("*")
                 if p.is_file() and p.name in REQUIRED]
    check(G, "design catalogs are not placed in data/raw or dbt seeds", not misplaced, misplaced or "data/design/crm only")
    extra = sorted(p.name for p in D.iterdir() if p.name not in REQUIRED)
    check(G, "design directory holds only the declared catalogs", not extra, extra or f"{len(REQUIRED)} files")
    log_ = C["design_change_log.csv"]
    fkeys = set(fld.table_name + "." + fld.column_name)
    bad_log = [r.change_id for r in log_.itertuples()
               if (r.change_type == "add_field" and r.object_id not in fkeys) or (r.change_type == "remove_field" and r.object_id in fkeys)
               or not (r.before and r.after and r.reason)
               or (r.change_type.startswith("update_") and r.catalog == "crm_field_dictionary.csv"
                   and fld.set_index(fld.table_name + "." + fld.column_name).loc[r.object_id, r.change_type[7:]] != r.after)]
    gp = C["synthetic_generation_parameters.csv"]
    check(G, "design changes are logged with before, after and reason and match the catalogs; generation settings and calendar are simulation settings",
          not bad_log and len(log_) > 0 and gp.approval_status.str.startswith("simulation setting").all()
          and C["business_calendar.csv"].approval_status.str.startswith("simulation setting").all(), bad_log or f"{len(log_)} changes, {len(gp)} parameters")

    new_texts = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in sorted(D.glob("*.csv"))}
    for p in (DOC_PROCESS, DOC_MODEL):
        if p.exists():
            new_texts[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^## CRM acquisition process design.*?(?=^## |\Z)", readme)
    readme_crm = m.group(0) if m else ""
    new_texts["README.md#crm-section"] = readme_crm
    emails = sorted({e for t in new_texts.values() for e in EMAIL_RE.findall(t)})
    check(G, "every email-like value uses a reserved .example domain", all(e.endswith(".example") for e in emails), emails or "none present")
    phones = sorted({p for t in new_texts.values() for p in PHONE_RE.findall(t)})
    check(G, "every phone-like value uses the reserved fictional 555-01xx range", all(re.search(r"555[-.\s]01\d\d", p) for p in phones), phones or "none present")
    ids = sorted({x for t in new_texts.values() for rx in (SIN_RE, BN_RE, ACCOUNT_RE) for x in rx.findall(t)})
    check(G, "no social-insurance, business-number or account-number patterns", not ids, ids or "none")
    name_fields = fld[fld.column_name.isin(["legal_name", "operating_name"])]
    check(G, "business-name examples are labelled fictional", len(name_fields) >= 2 and all("(fictional)" in x for x in name_fields.fictional_example))
    contact = fld[fld.column_name.str.endswith("_fictional")]
    check(G, "contact fields are fictional-only and excluded from future marts",
          len(contact) >= 2 and all("excluded from marts" in v.lower() for v in contact.validation_rule)
          and not fld[fld.privacy_class.str.contains("contact", case=False) & ~fld.column_name.str.endswith("_fictional")
                      & fld.column_name.str.contains("email|phone|address", regex=True)].shape[0], contact.column_name.tolist())
    lang = fld[(fld.table_name == "crm_lead") & (fld.column_name == "stated_service_language")]
    check(G, "service language is stated-only (default 'not stated'); no inferred personal characteristic fields",
          len(lang) == 1 and "not stated" in lang.accepted_values.iloc[0]
          and not fld.column_name.str.contains(r"(?:^|_)(?:gender|ethnicity|religion|age|birth|birth_date|date_of_birth|inferred)(?:_|$)", regex=True).any())
    bank_hits = sorted({(k, b) for k, t in new_texts.items() for b in REAL_BANKS if re.search(rf"\b{re.escape(b)}\b", t)})
    check(G, "no real bank or credit-union names in the design artifacts", not bank_hits, bank_hits or f"{len(REAL_BANKS)} names screened")
    phase_hits = sorted({(k, h) for k, t in new_texts.items() for h in PHASE_RE.findall(t)})
    new_names = [p.name for p in D.glob("*")] + [DOC_PROCESS.name, DOC_MODEL.name, Path(__file__).name] + [p.name for p in V.glob("*")]
    phase_names = [n for n in new_names if PHASE_RE.search(n.replace("_", " "))]
    check(G, "no internal project phase labels in new file names or content", not phase_hits and not phase_names, (phase_hits + phase_names) or "none")

    # ------------------------------------------------------------------ transitions
    G = "transition_validation"
    print("\nLead and assignment lifecycle")
    statuses, terminal = set(st.status_code), set(st[st.is_terminal == "true"].status_code)
    check(G, "terminal statuses are exactly CONVERTED, CLOSED_LOST, INVALID, MERGED, WITHDRAWN", terminal == REQUIRED_TERMINAL, sorted(terminal))
    unknown = tr[~tr.from_status.isin(statuses) | ~tr.to_status.isin(statuses)].transition_id.tolist()
    check(G, "every transition uses catalogued statuses", not unknown, unknown or f"{len(tr)} transitions")
    check(G, "every transition is triggered by a catalogued event", tr.trigger_event.isin(set(ev.event_code)).all())
    pairs = set(zip(tr.from_status, tr.to_status))
    check(G, "no duplicate (from, to) transition pairs", len(pairs) == len(tr))
    out_of_terminal = tr[tr.from_status.isin(terminal)]
    check(G, "terminal statuses leave only through audited reopening",
          (out_of_terminal.transition_type == "audited_reopening").all() and (out_of_terminal.to_status == "READY_FOR_ASSIGNMENT").all()
          and set(out_of_terminal.from_status) <= {"CLOSED_LOST", "WITHDRAWN"}, out_of_terminal.transition_id.tolist())
    reopen = tr[tr.transition_type == "audited_reopening"]
    check(G, "audited reopening requires approver role, reason and approval time and is audit-flagged",
          len(reopen) >= 1 and all({"reopen_reason", "approver_role", "approved_at"} <= set(split(r)) for r in reopen.required_fields)
          and reopen.audit_required.map(truthy).all() and "LEAD_REOPENED" in set(reopen.trigger_event), reopen.transition_id.tolist())
    into_term = tr[tr.to_status.isin(terminal)]
    check(G, "every transition into a terminal status requires a disposition",
          into_term.requires_disposition.map(truthy).all() and all("disposition_code" in split(r) for r in into_term.required_fields), f"{len(into_term)} transitions")
    check(G, "terminal statuses require a disposition in the status catalog; non-terminal do not",
          all(truthy(r.requires_disposition) == (r.status_code in terminal) for r in st.itertuples()))
    check(G, "every terminal status has at least one disposition and every disposition applies to a terminal status",
          set(disp.applies_to_status) == terminal, disp.applies_to_status.value_counts().to_dict())
    adj = {}
    for a, b in pairs:
        if a not in terminal:
            adj.setdefault(a, set()).add(b)
    seen, q = {"NEW"}, deque(["NEW"])
    while q:
        for n in adj.get(q.popleft(), ()):
            if n not in seen:
                seen.add(n)
                q.append(n)
    check(G, "every status is reachable from NEW through ordinary transitions", seen == statuses, sorted(statuses - seen) or "all reachable")
    can_end = set(terminal)
    changed = True
    while changed:
        changed = False
        for a, bs in adj.items():
            if a not in can_end and bs & can_end:
                can_end.add(a)
                changed = True
    check(G, "every non-terminal status can reach a terminal status", can_end == statuses, sorted(statuses - can_end) or "no dead ends")
    blocked = [p for p in NAMED_INVALID if p in pairs]
    check(G, "named invalid transitions are not allowed", not blocked, blocked or f"{len(NAMED_INVALID)} blocked")
    itx = C["invalid_transition_examples.csv"]
    ex_bad = [r.example_id for r in itx[itx.violation_type == "pair_not_allowed"].itertuples() if (r.from_status, r.to_status) in pairs]
    check(G, "invalid-transition examples of type pair_not_allowed are absent from the transition table", not ex_bad,
          ex_bad or f"{(itx.violation_type == 'pair_not_allowed').sum()} examples")
    guard = itx[itx.violation_type == "guard_not_met"]
    tr_idx = tr.set_index(["from_status", "to_status"])
    guard_ok = all((r.from_status, r.to_status) in pairs and truthy(tr_idx.loc[(r.from_status, r.to_status), "requires_disposition"])
                   for r in guard.itertuples())
    check(G, "invalid-transition examples of type guard_not_met are prevented by a required-field guard", guard_ok and set(itx.violation_type) <= {"pair_not_allowed", "guard_not_met"},
          guard.example_id.tolist())
    check(G, "CONVERTED only from QUALIFIED; ASSIGNED only from READY_FOR_ASSIGNMENT; CONTACTED only from ASSIGNED or NURTURING",
          {a for a, b in pairs if b == "CONVERTED"} == {"QUALIFIED"} and {a for a, b in pairs if b == "ASSIGNED"} == {"READY_FOR_ASSIGNMENT"}
          and {a for a, b in pairs if b == "CONTACTED"} == {"ASSIGNED", "NURTURING"})
    check(G, "status catalog entry events and SLA references resolve",
          st.entry_event.isin(set(ev.event_code)).all() and all(s in set(sla.sla_id) for v in st.applicable_sla_ids for s in split(v)))
    astat, aterm = set(asg.status_code), set(asg[asg.is_terminal == "true"].status_code)
    nexts_ok = all(set(split(r.allowed_next_statuses)) <= astat for r in asg.itertuples())
    term_ok = all((r.allowed_next_statuses == "") == truthy(r.is_terminal) for r in asg.itertuples())
    check(G, "assignment statuses: allowed next statuses resolve; terminal statuses have none", nexts_ok and term_ok, f"{len(asg)} statuses")
    check(G, "active assignment statuses are exactly QUEUED, ASSIGNED, ACKNOWLEDGED, REASSIGNMENT_PENDING",
          set(asg[asg.is_active == "true"].status_code) == {"QUEUED", "ASSIGNED", "ACKNOWLEDGED", "REASSIGNMENT_PENDING"}
          and not (asg.is_active.map(truthy) & asg.is_terminal.map(truthy)).any())
    aseen, q = {"QUEUED", "ASSIGNED"}, deque(["QUEUED", "ASSIGNED"])
    anext = {r.status_code: set(split(r.allowed_next_statuses)) for r in asg.itertuples()}
    while q:
        for n in anext.get(q.popleft(), ()):
            if n not in aseen:
                aseen.add(n)
                q.append(n)
    check(G, "every assignment status is reachable from QUEUED or ASSIGNED", aseen == astat, sorted(astat - aseen) or "all reachable")

    # ------------------------------------------------------------------ references
    G = "rule_reference_validation"
    print("\nRule and reference integrity")
    known = set()
    for f, k in KEYS.items():
        if f not in ("crm_entity_catalog.csv", "synthetic_volume_specification.csv"):
            known |= set(C[f][k])
    known |= set(ent.entity_id) | set(disp.disposition_code) | {s[:6] for s in sla.sla_id}  # SLA-06 names the SLA-06A..D family
    unresolved = {}
    for f, df in C.items():
        for col in df.columns:
            for v in df[col]:
                for ref in ID_RE.findall(v):
                    if ref not in known:
                        unresolved.setdefault(f, set()).add(ref)
    check(G, "every identifier referenced in any catalog resolves", not unresolved, {k: sorted(v) for k, v in unresolved.items()} or f"{len(known)} identifiers")
    sla_ev = {e for r in sla.itertuples() for e in [r.start_event] + split(r.stop_events)}
    check(G, "SLA start and stop events exist in the event catalog", sla_ev <= set(ev.event_code), sorted(sla_ev - set(ev.event_code)) or f"{len(sla_ev)} events")
    ch_sla = {s for v in ch.assignment_timing.tolist() + ch.first_contact_expectation.tolist() for s in re.findall(r"SLA-\d{2}[A-D]?", v)}
    check(G, "every channel names at least one SLA and every named SLA exists",
          ch_sla <= set(sla.sla_id) and all(re.search(r"SLA-\d{2}", a + b) for a, b in zip(ch.assignment_timing, ch.first_contact_expectation)), sorted(ch_sla))
    ind_keys = set(zip(dim_ind.industry_system.astype(str), dim_ind.industry_code.astype(str)))
    spc_keys = set(zip(spc.industry_system, spc.industry_code))
    check(G, "specialist industry codes exist in analytics.dim_industry", spc_keys <= ind_keys, sorted(spc_keys - ind_keys) or sorted(c for _, c in spc_keys))
    check(G, "specialist requirement levels are mandatory, preferred, excluded or not applicable; unclassified never allocated",
          set(spc.requirement_level) <= {"mandatory", "preferred", "excluded", "not applicable"}
          and spc[spc.industry_code == "UNCLASSIFIED"].specialist_type.eq("generalist").all(), spc.requirement_level.value_counts().to_dict())
    tier_col, prov_cols = "tier", ["province_code"]
    tiers = dim_cma[tier_col].astype(str).str.extract(r"(\d)")[0].value_counts().to_dict()
    check(G, "territory spine: 41 SGC 2021 CMAs with evidence tiers 24/11/6 in analytics.dim_cma",
          len(dim_cma) == 41 and tiers == {"1": 24, "2": 11, "3": 6}, f"{len(dim_cma)} CMAs; tiers {tiers}")
    code_col = "cma_code_sgc2021" if "cma_code_sgc2021" in dim_cma.columns else dim_cma.columns[0]
    row505 = dim_cma[dim_cma[code_col].astype(str) == "505"]
    multi = row505[prov_cols].astype(str).agg(" ".join, axis=1).str.contains("24").any() and row505[prov_cols].astype(str).agg(" ".join, axis=1).str.contains("35").any()
    check(G, "multi-province CMA example (505) exists and spans provinces 24 and 35", len(row505) == 1 and multi, prov_cols)
    fld_codes = {x for x in fld[fld.column_name == "cma_code_sgc2021"].fictional_example}
    check(G, "fictional CMA examples are valid SGC 2021 CMA codes", fld_codes <= set(dim_cma[code_col].astype(str)), sorted(fld_codes))
    ter_types = set(ter.territory_type)
    check(G, "territory rules cover CMA, provincial remainder, unresolved, out-of-footprint, remote-service and national-specialist cases",
          ter_types >= {"cma", "province_remainder", "unresolved", "out_of_footprint", "remote_service", "national_specialist"}, sorted(ter_types))
    refs = {(m.group(1), m.group(2)) for df in C.values() for col in df.columns for v in df[col] for m in ANALYTICS_RE.finditer(v)}
    bad_refs = sorted(f"{t}.{c}" if c else t for t, c in refs if t not in tables or (c and c not in tables[t]))
    check(G, "analytics tables and columns named as sources or foreign keys exist in the warehouse", not bad_refs,
          bad_refs or f"{len(refs)} references")
    fk_targets = {m for v in fld.foreign_key for m in re.findall(r"\b(crm_[a-z_]+)\b", v)}
    fk_targets |= {m for v in ent.foreign_keys for m in re.findall(r"->\s*(crm_[a-z_]+)", v)}
    check(G, "every CRM foreign key targets a catalogued entity", fk_targets <= entity_names, sorted(fk_targets - entity_names) or f"{len(fk_targets)} targets")
    fk_cols = {(t.strip(), c.strip()) for r in ent.itertuples() for part in split(r.foreign_keys) for c, t in [part.split("->")]
               if t.strip().startswith("crm_")}
    missing_fk_cols = []
    for r in ent.itertuples():
        for part in split(r.foreign_keys):
            col = part.split("->")[0].strip().strip("()")
            for c in [x.strip() for x in col.split(",")]:
                if c not in set(fld[fld.table_name == r.table_name].column_name):
                    missing_fk_cols.append(f"{r.table_name}.{c}")
    check(G, "every entity foreign-key column exists in its field contract", not missing_fk_cols, missing_fk_cols or f"{len(fk_cols)} CRM foreign keys")
    check(G, "every future dbt model follows the stg_crm__ naming convention", ent.future_dbt_model.str.match(r"^stg_crm__[a-z_]+$").all())
    kpi_marts = set(kpi.future_mart)
    check(G, "every KPI names a future mart (documented, not built)", kpi_marts and all(m.startswith("mart_crm_") for m in kpi_marts)
          and not any(m in tables for m in kpi_marts), sorted(kpi_marts))
    prio = pri.set_index("input_id")
    never = prio[prio.never_use == "true"]
    ctx = prio[prio.context_only == "true"]
    flags = ["may_affect_priority", "may_affect_routing_eligibility", "may_affect_sla"]
    check(G, "prohibited priority inputs affect nothing; context-only inputs affect nothing",
          len(never) >= 3 and not (never[flags + ["context_only"]] == "true").any().any() and not (ctx[flags] == "true").any().any(),
          f"{len(never)} prohibited, {len(ctx)} context-only")
    mkt = prio[prio.input_family == "market-context"]
    check(G, "market-context inputs never affect routing eligibility or SLA, and the score never overrides controls",
          not (mkt[["may_affect_routing_eligibility", "may_affect_sla"]] == "true").any().any()
          and any("never overrides" in n.lower() for n in mkt.note), f"{len(mkt)} market-context inputs")
    check(G, "priority inputs cover lead-level, market-context, operational and prohibited families",
          set(pri.input_family) == {"lead-level", "market-context", "operational", "prohibited"}, pri.input_family.value_counts().to_dict())

    # ------------------------------------------------------------------ requirements
    G = "requirements_coverage"
    print("\nRequirements coverage and traceability")
    acc_ids, sc_ids, ps_ids = set(acc.acceptance_criterion_id), set(scn.scenario_id), set(ps.step_id)
    t = trc.set_index("requirement_id")
    check(G, "every requirement appears once in the traceability matrix", set(t.index) == set(req.requirement_id) and t.index.is_unique, f"{len(t)} rows")
    must = req[req.moscow_priority == "Must"].requirement_id.tolist()
    gaps = {}
    for r in must:
        row = t.loc[r]
        miss = []
        if row.process_step_id not in ps_ids: miss.append("process step")
        if row.entity not in entity_names: miss.append("entity")
        if not split(row.acceptance_criterion_ids) or not set(split(row.acceptance_criterion_ids)) <= acc_ids: miss.append("acceptance criterion")
        if not split(row.scenario_ids) or not set(split(row.scenario_ids)) <= sc_ids: miss.append("scenario")
        if not split(row.future_test_names): miss.append("future test")
        if miss:
            gaps[r] = miss
    check(G, "every Must requirement traces to a process step, entity, acceptance criterion, scenario and future test", not gaps,
          gaps or f"{len(must)} Must requirements")
    acc_tests = dict(zip(acc.acceptance_criterion_id, acc.future_test_name))
    test_mismatch = [r.requirement_id for r in trc.itertuples() if split(r.future_test_names) != [acc_tests.get(a) for a in split(r.acceptance_criterion_ids)]]
    check(G, "traceability future tests match the acceptance-criteria catalog", not test_mismatch, test_mismatch or "consistent")
    req_acc = dict(zip(req.requirement_id, req.acceptance_criterion_ids))
    check(G, "requirement and traceability acceptance criteria agree", all(req_acc[r.requirement_id] == r.acceptance_criterion_ids for r in trc.itertuples()))
    used_acc = {a for v in req.acceptance_criterion_ids for a in split(v)}
    check(G, "every acceptance criterion is used by at least one requirement", used_acc == acc_ids, sorted(acc_ids - used_acc) or f"{len(acc_ids)} criteria")
    check(G, "every acceptance criterion's primary requirement exists", acc.primary_requirement_id.isin(set(req.requirement_id)).all())
    covered_steps = set(trc.process_step_id)
    check(G, "every process step is covered by at least one requirement", covered_steps >= ps_ids, sorted(ps_ids - covered_steps) or "20 of 20")
    ent_cov = set(trc.entity)
    check(G, "requirements reference at least 12 of the entities", len(ent_cov & entity_names) >= 12, f"{len(ent_cov & entity_names)} entities")
    kpis_used = {k for v in trc.kpi_id for k in split(v)}
    check(G, "requirement KPIs exist in the KPI catalog", kpis_used <= set(kpi.kpi_id), sorted(kpis_used - set(kpi.kpi_id)) or f"{len(kpis_used)} KPIs")
    check(G, "every stakeholder named in the needs register supports at least one requirement",
          all(r.related_requirements for r in C["stakeholder_needs.csv"].itertuples()))
    wont = req[req.moscow_priority == "Won't for current scope"]
    check(G, "production integration, ML and dashboards are explicitly out of scope", len(wont) >= 1, wont.requirement_id.tolist())
    for r in req.itertuples():
        row = t.loc[r.requirement_id]
        results[G].append({"check": f"trace {r.requirement_id}", "passed": True if r.moscow_priority != "Must" else r.requirement_id not in gaps,
                           "detail": f"{r.moscow_priority}; {row.process_step_id}; {row.entity}; {row.acceptance_criterion_ids}; {row.scenario_ids}"})

    # ------------------------------------------------------------------ scenarios
    G = "scenario_coverage"
    print("\nSynthetic scenario coverage")
    cond = dict(zip(scn.required_condition, scn.scenario_id))
    missing_cond = [c for c in REQUIRED_SCENARIO_CONDITIONS if c not in cond]
    check(G, "all 32 required scenario conditions are covered", not missing_cond, missing_cond or f"{len(REQUIRED_SCENARIO_CONDITIONS)} of 32")
    pair_id = {(a, b): i for i, a, b in zip(tr.transition_id, tr.from_status, tr.to_status)}
    path_bad = {}
    for r in scn.itertuples():
        seq = [s.strip() for s in r.expected_status_path.split(">")]
        seq = [s.split(" ")[0] for s in seq]
        bad = [s for s in seq if s not in statuses]
        steps = list(zip(seq, seq[1:]))
        ids = [pair_id.get(p) for p in steps]
        expected = split(r.transition_ids)
        if bad or None in ids or ids != expected:
            path_bad[r.scenario_id] = {"path": r.expected_status_path, "derived": ids, "declared": expected}
        ok = r.scenario_id not in path_bad
        results[G].append({"check": f"{r.scenario_id} path uses valid transitions", "passed": ok, "detail": r.expected_status_path})
    check(G, "every scenario status path uses only valid transitions matching its declared transition IDs", not path_bad, path_bad or f"{len(scn)} scenarios")
    all_refs = {x for v in scn.rule_ids.tolist() + scn.kpi_ids.tolist() + scn.acceptance_criterion_ids.tolist() for x in split(v)}
    check(G, "scenario rule, KPI and acceptance-criterion references resolve", all_refs <= known, sorted(all_refs - known) or f"{len(all_refs)} references")
    ch_cov = {c for v in scn.entities_involved.tolist() + scn.rule_ids.tolist() + scn.setup.tolist() for c in re.findall(r"CH-[A-Z]{3}", v)}
    check(G, "every channel is exercised by a scenario", set(CHANNELS) <= ch_cov | {"CH-DIG"} and {"CH-BRN", "CH-MKT", "CH-PTR", "CH-RMP"} <= ch_cov, sorted(ch_cov))
    final_status = {s.split(">")[-1].strip().split(" ")[0] for s in scn.expected_status_path}
    check(G, "every terminal status is reached by at least one scenario", terminal <= final_status, sorted(terminal - final_status) or sorted(terminal))
    used_tr = {x for v in scn.transition_ids for x in split(v)}
    check(G, "the audited reopening transition is exercised", bool(set(reopen.transition_id) & used_tr), sorted(set(reopen.transition_id) & used_tr))
    rule_cats = {rt.set_index("rule_id").rule_category.get(x) for v in scn.rule_ids for x in split(v)} - {None}
    check(G, "every routing-rule category is exercised by a scenario", set(ROUTING_CATEGORIES) <= rule_cats, sorted(set(ROUTING_CATEGORIES) - rule_cats) or "6 of 6")
    sla_cov = {x for v in scn.rule_ids.tolist() + scn.expected_sla_result.tolist() for x in re.findall(r"SLA-\d{2}[A-D]?", v)}
    check(G, "scenarios exercise acknowledgement, first-contact, follow-up, inactivity and reassignment SLAs",
          {"SLA-05", "SLA-06B", "SLA-07", "SLA-08A", "SLA-09"} <= sla_cov, sorted(sla_cov))
    exc_share = scn.exercises_exception_flag.map(truthy).mean()
    check(G, "scenarios mix normal and exception paths", 0.3 <= exc_share <= 0.8, f"{exc_share:.0%} exception scenarios")
    kpi_cov = {x for v in scn.kpi_ids for x in split(v)}
    fam_cov = set(kpi[kpi.kpi_id.isin(kpi_cov)].kpi_family)
    check(G, "scenarios feed every KPI family", fam_cov == set(KPI_FAMILIES), sorted(fam_cov))

    # ------------------------------------------------------------------ documentation
    G = "documentation_checks"
    print("\nDocumentation")
    docs = {p: p.read_text(encoding="utf-8") if p.exists() else "" for p in (DOC_PROCESS, DOC_MODEL)}
    check(G, "both design documents exist", all(docs.values()), [p.name for p in docs])
    check(G, "both documents and the README section carry the hypothetical-design disclaimer",
          all(DISCLAIMER in t.lower() for t in docs.values()) and DISCLAIMER in readme_crm.lower())
    check(G, "documents state that later CRM data will be entirely synthetic",
          all("entirely synthetic" in t.lower() for t in docs.values()))
    blocks = {p: re.findall(r"```mermaid\n(.*?)```", t, flags=re.S) for p, t in docs.items()}
    titles_ok = {}
    for p, want in EXPECTED_DIAGRAMS.items():
        heads = [b.lower() for b in blocks[p]]
        titles_ok[p.name] = [w for w in want if not any(f"title: {w}" in h for h in heads)]
    check(G, "six titled Mermaid diagrams (current state, target state, state machine, routing, SLA escalation, ERD)",
          sum(len(b) for b in blocks.values()) == 6 and not any(titles_ok.values()), {k: v for k, v in titles_ok.items() if v} or "6 of 6")
    sm = next((b for b in blocks[DOC_PROCESS] if "statediagram" in b.lower()), "")
    arrows = {(a, b) for a, b in re.findall(r"^\s*([A-Z_]+)\s*-->\s*([A-Z_]+)", sm, flags=re.M)}
    check(G, "state-machine diagram shows exactly the catalogued transitions", arrows == pairs,
          {"missing": sorted(pairs - arrows), "extra": sorted(arrows - pairs)} if arrows != pairs else f"{len(arrows)} transitions")
    erd = next((b for b in blocks[DOC_MODEL] if "erdiagram" in b.lower()), "")
    erd_ents = set(re.findall(r"\b(crm_[a-z_]+)\b", erd))
    check(G, "ERD shows every catalogued entity and no others", erd_ents == entity_names, sorted(entity_names ^ erd_ents) or f"{len(erd_ents)} entities")
    rf = next((b for b in blocks[DOC_PROCESS] if "routing decision flow" in b.lower()), "")
    rf_ids = set(re.findall(r"RT-[EPXLFO]\d{2}", rf))
    check(G, "routing flow diagram shows every routing rule", rf_ids == set(rt.rule_id), sorted(set(rt.rule_id) ^ rf_ids) or f"{len(rf_ids)} rules")
    ts = next((b for b in blocks[DOC_PROCESS] if "target-state acquisition process" in b.lower()), "")
    ts_ids = set(re.findall(r"PS-\d{2}", ts))
    check(G, "target-state diagram shows all 20 process steps", ts_ids == ps_ids, sorted(ps_ids ^ ts_ids) or "20 of 20")
    cs = next((b for b in blocks[DOC_PROCESS] if "hypothetical current state" in b.lower()), "")
    cs_ids = set(re.findall(r"CSP-\d{2}", cs))
    check(G, "current-state diagram is labelled hypothetical and shows all 13 illustrative problems",
          cs_ids == set(csp.problem_id) and "hypothetical" in cs.lower(), sorted(set(csp.problem_id) ^ cs_ids) or "13 of 13")
    se = next((b for b in blocks[DOC_PROCESS] if "sla escalation and reassignment" in b.lower()), "")
    check(G, "SLA escalation diagram references acknowledgement, first-contact, reassignment and escalation SLAs",
          all(s in se for s in ["SLA-05", "SLA-06", "SLA-09", "SLA-10"]))
    claims = {
        "steps": f"**{len(ps)}** process steps", "problems": f"**{len(csp)}** hypothetical current-state problems",
        "channels": f"**{len(ch)}** acquisition channels", "lead_statuses": f"**{len(st)}** lead statuses",
        "terminal": f"**{len(terminal)}** terminal statuses", "transitions": f"**{len(tr)}** allowed transitions",
        "reopening": f"**{len(reopen)}** audited reopening transitions", "dispositions": f"**{len(disp)}** dispositions",
        "events": f"**{len(ev)}** lifecycle events", "assignment_statuses": f"**{len(asg)}** assignment statuses",
        "routing_rules": f"**{len(rt)}** routing rules", "territory_rules": f"**{len(ter)}** territory rules",
        "specialist_rules": f"**{len(spc)}** specialist rules", "capacity_rules": f"**{len(cap)}** capacity rules",
        "sla_rules": f"**{len(sla)}** SLA rules", "duplicate_rules": f"**{len(dup)}** duplicate rules",
        "assumptions": f"**{len(asm)}** assumptions", "kpis": f"**{len(kpi)}** KPIs",
        "requirements": f"**{len(req)}** requirements", "must": f"**{len(must)}** Must",
        "acceptance": f"**{len(acc)}** acceptance criteria", "scenarios": f"**{len(scn)}** synthetic scenarios",
        "priority_inputs": f"**{len(pri)}** candidate priority inputs",
        "capacity_limits": f"warning at **{warn:.2f}**, hard limit at **{hard:.2f}**, exception ceiling **{exc:.2f}**",
        "base_capacity": f"**{float(capd.loc['CAP-05', 'threshold']):.0f}** workload points",
    }
    for fam_name, n in kpi.kpi_family.value_counts().sort_index().items():
        claims[f"kpi_{fam_name.replace(' ', '_')}"] = f"**{n}** {fam_name} KPIs"
    for p_, n in pref.value_counts().sort_index().items():
        claims[f"req_{p_}"] = f"{p_}: **{n}**"
    model_claims = {"entities": f"**{len(ent)}** entities", "fields": f"**{len(fld)}** fields",
                    "widest_table_fields": f"**{int(fcounts.max())}** fields in the widest table"}
    if "--print-claims" in sys.argv:
        for k, v in {**claims, **model_claims}.items():
            print(f"CLAIM {k}: {v}")
    miss_p = {k: v for k, v in claims.items() if v not in docs[DOC_PROCESS]}
    miss_m = {k: v for k, v in model_claims.items() if v not in docs[DOC_MODEL]}
    check(G, "process document counts reproduced by the catalogs", bool(docs[DOC_PROCESS]) and not miss_p,
          f"{len(claims) - len(miss_p)} of {len(claims)}" + (f"; missing {miss_p}" if miss_p else ""))
    check(G, "data-model document counts reproduced by the catalogs", bool(docs[DOC_MODEL]) and not miss_m,
          f"{len(model_claims) - len(miss_m)} of {len(model_claims)}" + (f"; missing {miss_m}" if miss_m else ""))
    doc_ids = {x for t in docs.values() for x in ID_RE.findall(t)}
    check(G, "every identifier cited in the documents exists in the catalogs", doc_ids <= known, sorted(doc_ids - known) or f"{len(doc_ids)} identifiers")
    table_names_in_doc = set(re.findall(r"`(crm_[a-z_]+)`", docs[DOC_MODEL]))
    check(G, "data-model document describes every entity", entity_names <= table_names_in_doc, sorted(entity_names - table_names_in_doc) or f"{len(entity_names)} entities")
    fact_like = [s for t in docs.values() for s in re.findall(r"[^.\n]*\b(?:the bank currently|our bank|in reality the bank|actual bank process)\b[^.\n]*", t, flags=re.I)]
    check(G, "documents make no factual claim about a real bank's internal process", not fact_like, fact_like or "none")
    check(G, "README documents the CRMDesign pipeline scope and links both design documents",
          "CRMDesign" in readme_crm and DOC_PROCESS.name in readme_crm and DOC_MODEL.name in readme_crm)

    # ------------------------------------------------------------------ outputs
    V.mkdir(parents=True, exist_ok=True)
    for name, rows in results.items():
        pd.DataFrame(rows, columns=["check", "passed", "detail"]).to_csv(V / f"{name}.csv", index=False, lineterminator="\n")
    allrows = [r for rows in results.values() for r in rows]
    summary = {
        "purpose": "Validation of the hypothetical CRM acquisition-process and requirements design (no CRM records exist)",
        "run_timestamp_utc": start,
        "validation_script_sha256": sha(Path(__file__)),
        "opportunity_validation": {"checks_passed": opp.get("checks_passed"), "checks_total": opp.get("checks_total")},
        "design_counts": {"assumptions": len(asm), "stakeholder_needs": len(C["stakeholder_needs.csv"]), "process_steps": len(ps),
                          "current_state_problems": len(csp), "channels": len(ch), "lead_statuses": len(st), "terminal_statuses": sorted(terminal),
                          "transitions": len(tr), "audited_reopening_transitions": len(reopen), "dispositions": len(disp), "events": len(ev),
                          "assignment_statuses": len(asg), "routing_rules": rt.rule_category.value_counts().sort_index().to_dict(),
                          "territory_rules": len(ter), "specialist_rules": len(spc), "capacity_rules": len(cap), "sla_rules": len(sla),
                          "duplicate_rules": len(dup), "entities": len(ent), "fields": len(fld),
                          "fields_by_entity": {k: int(v) for k, v in fcounts.sort_index().items()},
                          "kpis": kpi.kpi_family.value_counts().sort_index().to_dict(),
                          "requirements": pref.value_counts().sort_index().to_dict(),
                          "requirements_by_priority": req.moscow_priority.value_counts().sort_index().to_dict(),
                          "acceptance_criteria": len(acc), "scenarios": len(scn), "priority_inputs": len(pri)},
        "doc_claims": {**claims, **model_claims},
        "catalog_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(D.glob("*.csv"))},
        "document_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in (DOC_PROCESS, DOC_MODEL) if p.exists()},
        "evidence_sha256": {f"data/validation/crm_design/{n}.csv": sha(V / f"{n}.csv") for n in results},
        "checks_by_file": {n: {"passed": sum(r["passed"] for r in rows), "total": len(rows)} for n, rows in results.items()},
        "checks_passed": sum(r["passed"] for r in allrows), "checks_total": len(allrows),
        "failed_checks": [r["check"] for r in allrows if not r["passed"]],
        "all_checks_pass": all(r["passed"] for r in allrows),
    }
    (V / "crm_design_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"\nChecks: {summary['checks_passed']} of {summary['checks_total']} pass")
    if not summary["all_checks_pass"]:
        print("CRM DESIGN VALIDATION FAILED")
        sys.exit(1)
    print("CRM DESIGN VALIDATION PASSED")


if __name__ == "__main__":
    main()
