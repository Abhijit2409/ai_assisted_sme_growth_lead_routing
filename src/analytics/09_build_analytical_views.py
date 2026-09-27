"""
Phase G — build, populate and validate the analytical data layer specified in Phase F
for the Canadian SME Acquisition Strategy.

Scope: the 11 views in data/validation/mvp_grain/analytical_model_spec.csv, populated
from the lineage-checked caches and the Phase E crosswalks. Each fact view is fed by one
source only and carries source, statistical unit, period and scope on every row. No
opportunity scores, rankings, priorities, synthetic CRM data, routing rules or dashboards
are produced, and public aggregates are never turned into leads.

Design:
- Verifies Phases A-F (summaries), every raw file against data/source_manifest.csv, the
  StatCan cache lineage (check only: a stale cache fails the run, it is never rebuilt), the
  ISED cell cache against Phase C, and snapshots every protected input (caches, crosswalks,
  Phase A-F outputs) so the run can prove it changed none of them.
- Joins use codes and crosswalk keys. ISED publishes no codes, so its stub text is looked up
  once, exactly and within its row context, in the reviewed Phase E crosswalks (E2, E4, E8)
  and only the resulting codes are used downstream; every lookup is registered and gated.
- Unavailable (x, ..) values stay NULL. A 1097 cell is zero only when it is an omitted cell
  inside a CMA, which Phase B proved zero by reconciliation; such cells are counted per row.
- Views are written to a temporary directory, validated, and only then moved onto the exact
  target files. Any failed gate exits with status 1.

Outputs:
  data/processed/analytical_views/<view>.parquet and analytical_views_manifest.csv
  data/validation/analytical_views/ (checks, reconciliations, deviations, phase_g_summary.json)

Run (Anaconda interpreter; works from any working directory):
    C:/Users/abhij/anaconda3/python.exe src/analytics/09_build_analytical_views.py
"""

from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import time

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True          # keep src/ free of __pycache__ when importing the helper
sys.path.insert(0, str(ROOT / "src" / "validation"))
from cache_lineage import CACHE_SCHEMAS, check_lineage, expected_columns, sha256_file  # noqa: E402

BUILD_VERSION = "phase-g/1.0"
DATA = ROOT / "data"
VAL = DATA / "validation"
XW = DATA / "reference" / "crosswalks"
MVP = VAL / "mvp_grain"
OUT = DATA / "processed" / "analytical_views"
VOUT = VAL / "analytical_views"
MANIFEST = DATA / "source_manifest.csv"
DOC04 = ROOT / "docs" / "04_analytical_data_layer.md"
DOC02 = ROOT / "docs" / "02_data_sources_and_validation.md"
RAW_0722 = DATA / "raw" / "statcan_33100722" / "33100722-eng" / "33100722.csv"
RAW_1097 = DATA / "raw" / "statcan_33101097" / "33101097-eng" / "33101097.csv"
CACHE_0722 = DATA / "cache" / "statcan_33100722.parquet"
CACHE_1097 = DATA / "cache" / "statcan_33101097.parquet"
CACHE_ISED = DATA / "cache" / "ised_sfgsme_2023_cells.parquet"
SUMMARIES = {"A": VAL / "33100722" / "phase_a_summary.json", "B": VAL / "33101097" / "phase_b_summary.json",
             "C": VAL / "ised_2023" / "phase_c_summary.json", "D": VAL / "source_comparison" / "phase_d_summary.json",
             "E": VAL / "crosswalks" / "phase_e_summary.json", "F": MVP / "phase_f_summary.json"}
XW_FILES = {"E1": "cma_sgc2016_to_sgc2021.csv", "E2": "ised_cma_to_sgc2021.csv", "E3": "statcan_0722_industry_to_naics2022.csv",
            "E4": "ised_industry_to_naics2022.csv", "E5": "statcan_1097_size_to_sme_bands.csv", "E6": "ised_scope_approximation_rules.csv",
            "E7": "non_additive_category_rules.csv", "E8": "ised_region_province_crosswalk.csv"}
PROTECTED_DIRS = [DATA / "cache", XW, VAL / "33100722", VAL / "33101097", VAL / "ised_2023", VAL / "source_comparison",
                  VAL / "crosswalks", MVP]

SRC_0722, SRC_1097, SRC_ISED = "StatCan 33-10-0722-01", "StatCan 33-10-1097-01", "ISED SFGSME 2023"
PERIOD_1097, REF_DATE_1097 = "December 2025", "2025-07"
PANDEMIC = ("2020-03", "2021-12")

# Reviewed code maps. Each is verified against the published member labels by a gate; none is a join on labels.
SIZE_0722 = {1: ("TOTAL", "Total, all employment sizes"), 2: ("1-4", "1 to 4 employees"), 3: ("5-19", "5 to 19 employees"),
             4: ("20-99", "20 to 99 employees"), 5: ("100-499", "100 to 499 employees"), 6: ("500-plus", "500 employees or more")}
MEASURES_0722 = {1: ("active", "Active businesses"), 2: ("openings", "Opening businesses"),
                 3: ("continuing", "Continuing businesses"), 4: ("closures", "Closing businesses")}
EXCLUDED_MEASURES_0722 = {5: "Reopening businesses", 6: "Entrants", 7: "Temporary closures", 8: "Exits"}
ISED_SIZE = {"1 to 4 employees": "1-4", "5 to 19 employees": "5-19", "20 to 99 employees": "20-99", "100 to 499 employees": "100-499"}
ISED_NATIONAL_LABEL = "1 to 499 employees"
ISED_AGG_CODES = {"Information and Communication Technologies (ICT)": "AGG-ICT", "Knowledge-Based Industries (KBI)": "AGG-KBI",
                  "ALL Tourism": "AGG-TOURISM", "Transportation": "AGG-TRANSPORTATION",
                  "Arts, Entertainment and Recreation": "AGG-ARTS-ENT-REC", "Accommodation services": "AGG-ACCOMMODATION",
                  "Food services and drinking places": "AGG-FOOD-SERVICES", "Other": "AGG-OTHER"}
SUBPROV_KEYS = {"Northern Ontario": "35-N", "Southern Ontario": "35-S"}
E7_LINKS = {"N01": ("NAICS2022_1097", "UNCLASSIFIED"), **{f"N{i:02d}": ("STATCAN_0722", str(i + 18)) for i in range(2, 12)},
            "N12": ("STATCAN_0722", "1"), "N13": ("STATCAN_0722", "32"), "N14": ("STATCAN_0722", "30"), "N15": ("STATCAN_0722", "31"),
            **{f"N{16 + i}": ("ISED_SFGSME_2023", c) for i, c in enumerate(ISED_AGG_CODES.values())},
            "N24": ("ISED_REGION", "35-N"), "N25": ("ISED_REGION", "35-S"), "N26": ("ISED_REGION", "46"),
            "N27": ("ISED_REGION", "47"), "N28": ("ISED_REGION", "48")}
ISED_SEGMENT_DIMS = {"all_smes": "national", "employment_size": "size_band", "geography_cma": "cma",
                     "geography_region": "region", "industry_naics_group": "industry_group"}
ISED_SCOPE = {"1": set(ISED_SEGMENT_DIMS), "3": set(ISED_SEGMENT_DIMS), "4": set(ISED_SEGMENT_DIMS), "49": set(ISED_SEGMENT_DIMS),
              "5": {"all_smes", "employment_size"}}                     # I05: deal size national and size-band only
TABLE_50_REASON = "Table 50: two-way cross (region columns x industry rows); outside the one-way segment grain"

VIEW_ORDER = ["dim_cma", "dim_industry", "dim_size_band", "fact_cma_momentum_monthly", "fact_cma_market_scale_snapshot",
              "fact_cma_sector_composition_snapshot", "fact_cma_size_composition_snapshot", "fact_ised_financing_context",
              "bridge_cma_vintage", "bridge_industry", "bridge_size_band"]
FACT_PERIOD = {"fact_cma_momentum_monthly": "ref_month", "fact_cma_market_scale_snapshot": "snapshot_period",
               "fact_cma_sector_composition_snapshot": "snapshot_period", "fact_cma_size_composition_snapshot": "snapshot_period",
               "fact_ised_financing_context": "survey_year"}
NULLABLE_PK_PARTS = {"bridge_cma_vintage": {"cma_code_sgc2016"}, "bridge_industry": {"target_code"}}

gates, checks, JOINS, DOC_CLAIMS = {}, [], [], {}


def gate(name, passed, detail="", view="general"):
    gates[name] = bool(passed)
    checks.append({"check": name, "view": view, "passed": bool(passed), "detail": str(detail)})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def banner(t):
    print("\n" + "=" * 90 + "\n" + t + "\n" + "=" * 90)


def kjoin(name, left, right, left_on, right_on=None, how="left", validate=None):
    right_on = right_on or left_on
    JOINS.append({"join": name, "left_keys": ";".join(left_on), "right_keys": ";".join(right_on), "kind": "code"})
    return left.merge(right, left_on=left_on, right_on=right_on, how=how, validate=validate)


def sqljoin(name, keys):
    JOINS.append({"join": name, "left_keys": ";".join(keys), "right_keys": ";".join(keys), "kind": "code"})


def csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def tf(s):
    return s.map({"True": True, "False": False}).astype(bool)


def snapshot():
    files = sorted(p for d in PROTECTED_DIRS for p in d.rglob("*") if p.is_file())
    return {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in files}


# ---------------------------------------------------
# 1. Upstream integrity
# ---------------------------------------------------

def upstream(con):
    banner("1. UPSTREAM INTEGRITY")
    for k, p in SUMMARIES.items():
        s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        g = s.get("gates", {})
        gate(f"upstream_phase_{k}_passes", bool(g) and all(g.values()) and s.get("all_gates_pass") is True, f"{sum(g.values())} of {len(g)}")
    manifest = csv(MANIFEST)
    shas = {r.relative_path: sha256_file(ROOT / r.relative_path) for r in manifest.itertuples()}
    gate("raw_files_match_manifest_before", all(shas[r.relative_path] == r.sha256 for r in manifest.itertuples()), f"{len(manifest)} files")
    for key, raw, cache in (("statcan_33100722", RAW_0722, CACHE_0722), ("statcan_33101097", RAW_1097, CACHE_1097)):
        ok, why, _ = check_lineage(con, root=ROOT, raw_path=raw, cache_path=cache, manifest_path=MANIFEST,
                                   schema_version=CACHE_SCHEMAS[key]["version"], expected_columns=expected_columns(key, raw),
                                   source_sha256=shas[raw.relative_to(ROOT).as_posix()])
        gate(f"cache_lineage_current_{key}", ok, why)
    e = json.loads(SUMMARIES["E"].read_text(encoding="utf-8"))
    gate("all_eight_crosswalks_exist_and_pass", all((XW / f).exists() for f in XW_FILES.values())
         and e["facts"]["checks_passed"] == e["facts"]["checks"], f"{e['facts']['checks_passed']} of {e['facts']['checks']} crosswalk checks")
    c = json.loads(SUMMARIES["C"].read_text(encoding="utf-8"))
    vs = dict(con.execute(f"select value_state, count(*) from read_parquet('{CACHE_ISED.as_posix()}') group by 1").fetchall())
    gate("ised_cache_matches_phase_c", vs == c["value_states"] and sum(vs.values()) == c["facts"]["value_cells"],
         f"{sum(vs.values()):,} cells; {vs}")
    spec = csv(MVP / "analytical_model_spec.csv")
    gate("phase_f_specifies_the_11_views", spec.view.tolist() == VIEW_ORDER, f"{len(spec)} views")
    return manifest, shas, spec, c["facts"]


# ---------------------------------------------------
# 2. Dimensions and bridges
# ---------------------------------------------------

def region_keys(e8):
    """One segment key per ISED region/province row: the SGC province-code set it covers (sub-provincial rows get their own key)."""
    keys = {}
    for lab, g in e8.groupby("source_label", sort=False):          # E8 key column is source_label (Phase E KEYCOL)
        keys[lab] = SUBPROV_KEYS[lab] if (g.mapping_status == "unresolved").all() else ";".join(sorted(g.target_code))
    return keys


def ised_code(code, label):
    return code if code else ISED_AGG_CODES[label]


def build_bridges(e1, e3, e4, e5):
    b_cma = pd.DataFrame({
        "cma_code_sgc2016": e1.source_code.replace("", None), "cma_code_sgc2021": e1.target_code,
        "dguid_2016": e1.source_dguid.replace("", None), "dguid_2021": e1.target_dguid,
        "cma_label_sgc2016": e1.source_label.replace("", None), "cma_label_sgc2021": e1.target_label,
        "mapping_type": e1.mapping_type, "mapping_status": e1.mapping_status, "boundary_change_flag": e1.boundary_change_flag,
        "preliminary_csd_additions": pd.to_numeric(e1.preliminary_csd_additions.replace("", None)).astype("Int64"),
        "has_2016_source": e1.source_code != "", "exact_boundary_claimed": False,
        "crosswalk_id": e1.crosswalk_id, "evidence_url": e1.evidence_url})
    ind3 = pd.DataFrame({
        "source_system": "STATCAN_0722", "source_code": e3.source_member_id, "source_published_code": e3.source_code.replace("", None),
        "source_label": e3.source_label, "target_system": "NAICS2022", "target_code": e3.target_code.replace("", None),
        "target_label": e3.target_label.replace("", None), "mapping_type": e3.mapping_type, "mapping_status": e3.mapping_status,
        "additive_flag": tf(e3.sector_rollup_flag), "inclusion_type": e3.source_class, "excluded_codes_within": None,
        "confidence": e3.confidence, "crosswalk_id": e3.crosswalk_id})
    ind4 = pd.DataFrame({
        "source_system": "ISED_SFGSME_2023", "source_code": [ised_code(c, l) for c, l in zip(e4.source_code, e4.source_label)],
        "source_published_code": None, "source_label": e4.source_label, "target_system": "NAICS2022",
        "target_code": e4.target_code.replace("", None), "target_label": e4.target_label.replace("", None),
        "mapping_type": e4.mapping_type, "mapping_status": e4.mapping_status, "additive_flag": tf(e4.additive_flag),
        "inclusion_type": e4.inclusion_type, "excluded_codes_within": e4.excluded_codes_within.replace("", None),
        "confidence": e4.confidence, "crosswalk_id": e4.crosswalk_id})
    b_ind = pd.concat([ind3, ind4], ignore_index=True)
    b_ind["value_allocation_allowed"] = False
    b_size = pd.DataFrame({
        "source_band": e5.source_code, "source_label": e5.source_label, "target_band": e5.target_code, "target_label": e5.target_label,
        "source_lower": pd.to_numeric(e5.source_lower.replace("", None)).astype("Int64"),
        "source_upper": pd.to_numeric(e5.source_upper.replace("", None)).astype("Int64"),
        "mapping_type": e5.mapping_type, "mapping_status": e5.mapping_status, "unit_basis": e5.unit_basis,
        "in_ised_scope": e5.in_ised_scope.map({"True": True, "False": False, "": None}).astype("boolean"),
        "crosswalk_id": e5.crosswalk_id})
    return b_cma, b_ind, b_size


def build_dim_cma(e1, e2, e8, rkeys):
    spine = csv(MVP / "selected_reporting_spine.csv")
    geo = csv(VAL / "33101097" / "geography_profile.csv")
    geo = geo[geo.geo_level == "CMA"][["geo_code", "dguid", "province_code"]]
    d = kjoin("spine_to_1097_cma_profile", spine, geo, ["cma_code_sgc2021"], ["geo_code"], validate="one_to_one")
    d = kjoin("spine_to_E1", d, e1[["target_code", "source_dguid", "target_dguid", "preliminary_csd_additions"]],
              ["cma_code_sgc2021"], ["target_code"], validate="one_to_one")
    d = kjoin("spine_to_E2", d, e2[["target_code", "boundary_change_flag"]].rename(columns={"target_code": "e2_code", "boundary_change_flag": "e2_caveat"}),
              ["cma_code_sgc2021"], ["e2_code"], validate="one_to_one")
    prov = []
    for pc in sorted(d.province_code.unique()):          # a CMA spanning two provinces (Ottawa-Gatineau, "24;35") keeps both
        keys = []
        for p in pc.split(";"):
            rows = e8[(e8.target_code == p) & (e8.mapping_status != "unresolved")]
            indiv = rows[rows.dimension_type == "individual province"]
            keys.append(rkeys[(indiv if len(indiv) else rows).source_label.iloc[0]])
        prov.append({"province_code": pc, "ised_region_key": "|".join(keys)})
    d = kjoin("cma_province_code_to_E8_region", d, pd.DataFrame(prov), ["province_code"], validate="many_to_one")
    tier = d.tier.astype(int)
    desc = {1: "Momentum (0722 enterprises, monthly to 2026-05) + scale and composition (1097 locations, December 2025) + ISED CMA context (2023 survey estimates)",
            2: "Momentum (0722 enterprises, monthly to 2026-05) + scale and composition (1097 locations, December 2025); ISED at region or national level only, no CMA estimate",
            3: "Scale and composition only (1097 locations, December 2025); a CA in 2016, so no 0722 momentum series; never compared on momentum with Tiers 1-2"}
    out = pd.DataFrame({
        "cma_code_sgc2021": d.cma_code_sgc2021, "cma_label_sgc2021": d.cma_label_sgc2021, "dguid_2021": d.dguid_2021,
        "province_code": d.province_code, "cma_code_sgc2016": d.cma_code_sgc2016.replace("", None), "dguid_2016": d.dguid_2016.replace("", None),
        "vintage_status": d.vintage_status, "boundary_change_flag": d.boundary_change_flag,
        "preliminary_csd_additions": pd.to_numeric(d.preliminary_csd_additions.replace("", None)).astype("Int64"),
        "tier": tier, "in_primary_decision_set": tf(d.in_primary_decision_set), "momentum_comparison_eligible": tier.isin([1, 2]),
        "has_momentum_0722": tf(d.has_momentum_0722), "has_scale_1097": tf(d.has_scale_1097), "has_ised_cma_context": tf(d.has_ised_cma_context),
        "ised_cma_label": d.ised_cma_label.replace("", None), "ised_region_key": d.ised_region_key,
        "ised_geography_caveat": d.e2_caveat.astype(object).where(d.e2_caveat.notna(), None),
        "evidence_label": d.evidence_label, "evidence_coverage_description": tier.map(desc)})
    consistent = bool(((d.dguid_2021 == d.dguid) & (d.dguid_2021 == d.target_dguid)
                       & ((d.cma_code_sgc2016 == "") | (d.dguid_2016 == d.source_dguid))).all())
    return out.sort_values("cma_code_sgc2021").reset_index(drop=True), consistent


def build_dim_industry(e3, e4, e7, e8, rkeys):
    npf = csv(VAL / "33101097" / "naics_profile.csv")
    special = {"table_total": "TOTAL", "unclassified": "UNCLASSIFIED", "classified_subtotal": "CLASSIFIED_SUBTOTAL"}
    npf["code"] = [special.get(l, c) for l, c in zip(npf.naics_level, npf.naics_code)]
    id2code = dict(zip(npf.naics_id, npf.code))
    parent = dict(zip(npf.naics_id, npf.derived_parent_id))
    level = dict(zip(npf.naics_id, npf.naics_level))

    def sector_of(i):
        while level[i] not in ("sector", "table_total", "unclassified", "classified_subtotal"):
            i = parent[i]
        return i
    sec = {i: sector_of(i) for i in npf.naics_id}
    role = []
    for l, c in zip(npf.naics_level, npf.code):
        if l == "sector":
            role.append("composition_sector" if c != "91" else "shown_separately_outside_sme_banking_scope")
        elif l == "unclassified":
            role.append("shown_separately_never_allocated")
        else:
            role.append("not_in_mvp_view")
    n = pd.DataFrame({
        "industry_system": "NAICS2022_1097", "industry_code": npf.code, "industry_label": npf.naics, "industry_level": npf.naics_level,
        "parent_code": [id2code.get(parent[i]) if parent[i] else None for i in npf.naics_id],
        "in_mvp": [r != "not_in_mvp_view" for r in role],
        "in_business_sector_scope": [level[sec[i]] == "sector" and id2code[sec[i]] != "91" for i in npf.naics_id],
        "additive_flag": [l in ("sector", "subsector", "industry_group") for l in npf.naics_level],
        "overlap_flag": [l in ("table_total", "classified_subtotal") for l in npf.naics_level],
        "mvp_role": role, "naics_codes": [c if l in ("sector", "subsector", "industry_group") else None for l, c in zip(npf.naics_level, npf.code)],
        "classification_vintage": "NAICS 2022 (1097 Note 5)", "mapping_status": "native", "source_member_id": npf.naics_id})
    mvp_0722 = set(e3[(e3.source_class == "naics_sector") & (e3.sector_rollup_flag == "True")].source_member_id) | {"1"}
    rows = []
    for m, g in e3.groupby("source_member_id", sort=False):
        st = g.mapping_status.unique()
        rows.append({"industry_system": "STATCAN_0722", "industry_code": m, "industry_label": g.source_label.iloc[0],
                     "industry_level": g.source_class.iloc[0], "parent_code": g.source_parent_id.iloc[0] or None, "in_mvp": m in mvp_0722,
                     "in_business_sector_scope": True, "additive_flag": g.sector_rollup_flag.iloc[0] == "True",
                     "overlap_flag": g.sector_rollup_flag.iloc[0] != "True",
                     "mvp_role": ("momentum_business_sector_total" if m == "1" else "momentum_sector_member") if m in mvp_0722 else "not_in_mvp_view",
                     "naics_codes": ";".join(t for t in g.target_code if t) or None, "classification_vintage": "NAICS vintage not stated (0722)",
                     "mapping_status": st[0] if len(st) == 1 else "MIXED", "source_member_id": m})
    for lab, g in e4.groupby("source_label", sort=False):
        grp = g.inclusion_type.iloc[0] != "overlapping aggregate"
        st = g.mapping_status.unique()
        rows.append({"industry_system": "ISED_SFGSME_2023", "industry_code": ised_code(g.source_code.iloc[0], lab), "industry_label": lab,
                     "industry_level": "industry_group" if grp else "special_aggregate", "parent_code": None, "in_mvp": grp,
                     "in_business_sector_scope": True, "additive_flag": g.additive_flag.iloc[0] == "True", "overlap_flag": not grp,
                     "mvp_role": "financing_context_group" if grp else "not_in_mvp_view",
                     "naics_codes": ";".join(t for t in g.target_code if t) or None, "classification_vintage": "NAICS 2022 sectors via E4",
                     "mapping_status": st[0] if len(st) == 1 else "MIXED", "source_member_id": None})
    d = pd.concat([n, pd.DataFrame(rows)], ignore_index=True)
    # E7 rules carry no codes: they are linked through the reviewed E7_LINKS map and each link is verified by exact label.
    unc = npf[npf.naics_level == "unclassified"].iloc[0]
    labels = {("NAICS2022_1097", "UNCLASSIFIED"): f"{unc.naics} [{unc.naics_id}]"}
    labels.update({("STATCAN_0722", m): l for m, l in zip(e3.source_member_id, e3.source_label)})
    labels.update({("ISED_SFGSME_2023", ISED_AGG_CODES[l]): l for l in e4.source_label if l in ISED_AGG_CODES})
    labels.update({("ISED_REGION", rkeys[l]): l for l in e8.source_label})
    verified = {rid: labels.get(E7_LINKS.get(rid)) == lab for rid, lab in zip(e7.rule_id, e7.source_label)}
    link = {v: k for k, v in E7_LINKS.items()}
    d["non_additive_rule_id"] = [link.get((s, c)) for s, c in zip(d.industry_system, d.industry_code)]
    d.loc[(d.industry_system == "NAICS2022_1097") & (d.industry_code == "UNCLASSIFIED"), "additive_flag"] = False   # E7 N01
    sector_ids = [int(i) for i in npf[npf.naics_level == "sector"].naics_id] + [int(unc.naics_id)]
    return d.sort_values(["industry_system", "industry_code"]).reset_index(drop=True), verified, id2code, sector_ids


def build_dim_size(e5):
    t = e5.drop_duplicates("target_code")
    s0722 = {k: i for i, (k, _) in SIZE_0722.items()}
    ised = {v: k for k, v in ISED_SIZE.items()}
    order = ["1-4", "5-19", "20-99", "100-499", "500-plus", "TOTAL"]
    d = pd.DataFrame({
        "size_band_key": t.target_code, "size_band_label": t.target_label,
        "lower_employees": pd.to_numeric(t.target_lower.replace("", None)).astype("Int64"),
        "upper_employees": pd.to_numeric(t.target_upper.replace("", None)).astype("Int64"),
        "sort_order": [order.index(k) + 1 for k in t.target_code], "is_total": t.target_code == "TOTAL",
        "in_sme_scope": t.target_code.isin(["1-4", "5-19", "20-99", "100-499"]),
        "source_0722_size_member_id": [s0722[k] for k in t.target_code],
        "source_1097_size_member_ids": [";".join(e5[e5.target_code == k].source_code) for k in t.target_code],
        "ised_segment_label": [ised.get(k) for k in t.target_code],
        "unit_basis": "classification only; the statistical unit (enterprise, location or survey estimate) is carried on each fact and bridge row"})
    return d.sort_values("sort_order").reset_index(drop=True)


# ---------------------------------------------------
# 3. Facts
# ---------------------------------------------------

def build_momentum(con, e3, b_cma):
    mvp = sorted(int(m) for m in set(e3[(e3.source_class == "naics_sector") & (e3.sector_rollup_flag == "True")].source_member_id) | {"1"})
    con.register("b_cma_df", b_cma[b_cma.cma_code_sgc2016.notna()][["cma_code_sgc2016", "cma_code_sgc2021", "boundary_change_flag"]])
    con.register("size_map", pd.DataFrame([(i, k) for i, (k, _) in SIZE_0722.items()], columns=["size_id", "size_band_key"]))
    con.register("measure_map", pd.DataFrame([(i, k, l) for i, (k, l) in MEASURES_0722.items()], columns=["measure_id", "measure", "measure_label"]))
    sqljoin("0722_cma_code_to_bridge_cma_vintage", ["cma_code_sgc2016"])
    sqljoin("0722_size_id_to_size_map", ["size_id"])
    sqljoin("0722_measure_id_to_measure_map", ["measure_id"])
    sqljoin("derived_rates_same_series_previous_month", ["cma_code_sgc2016", "size_id", "industry_id", "ref_month"])
    ok_ = "in ('', 'E')"
    con.execute(f"""
        create or replace temp table pub as
        select right(a.DGUID, 3) as cma_code_sgc2016, a.REF_DATE as ref_month, a.size_id, a.industry_id, a.measure_id,
               a.value_num, coalesce(a.STATUS, '') as status_raw, a.VECTOR as vector
        from read_parquet('{CACHE_0722.as_posix()}') a
        where a.DGUID like '2016S0503%' and a.measure_id in (1, 2, 3, 4) and a.industry_id in ({','.join(map(str, mvp))})""")
    con.execute("""
        create or replace temp table w as
        select cma_code_sgc2016, ref_month, size_id, industry_id,
               max(case when measure_id = 1 then value_num end) act, max(case when measure_id = 1 then status_raw end) act_s,
               bool_or(measure_id = 1) has_act,
               max(case when measure_id = 2 then value_num end) opn, max(case when measure_id = 2 then status_raw end) opn_s,
               bool_or(measure_id = 2) has_opn,
               max(case when measure_id = 4 then value_num end) cls, max(case when measure_id = 4 then status_raw end) cls_s,
               bool_or(measure_id = 4) has_cls
        from pub group by all""")
    con.execute(f"""
        create or replace temp table m as
        with j as (
            select w.*, p.act as pact, p.act_s as pact_s, coalesce(p.has_act, false) as has_pact
            from w left join w p
              on p.cma_code_sgc2016 = w.cma_code_sgc2016 and p.size_id = w.size_id and p.industry_id = w.industry_id
             and p.ref_month = strftime(strptime(w.ref_month || '-01', '%Y-%m-%d') - interval 1 month, '%Y-%m')
            where w.has_opn or w.has_cls)
        select *, coalesce(opn_s {ok_} and opn is not null, false) as opn_ok, coalesce(cls_s {ok_} and cls is not null, false) as cls_ok,
               coalesce(has_pact and pact_s {ok_} and pact is not null and pact > 0, false) as pact_ok,
               coalesce(opn_s = 'E', false) as opn_e, coalesce(cls_s = 'E', false) as cls_e, coalesce(pact_s = 'E', false) as pact_e
        from j""")

    def rate(measure, label, formula, num, num_ok, num_e):
        return f"""select cma_code_sgc2016, ref_month, size_id, industry_id, '{measure}' as measure, '{label}' as measure_label,
                          '{formula}' as formula, case when {num_ok} and pact_ok then ({num}) / pact end as value,
                          case when not has_pact then 'no_prior_month' when not ({num_ok} and pact_ok) then 'unavailable_input'
                               when {num_e} or pact_e then 'derived_E' else 'derived' end as value_state from m"""
    con.execute(f"""
        create or replace temp table der as
        select cma_code_sgc2016, ref_month, size_id, industry_id, 'net_formation' as measure, 'Net formation (openings - closures)' as measure_label,
               'openings(t) - closures(t)' as formula, case when opn_ok and cls_ok then opn - cls end as value,
               case when not (opn_ok and cls_ok) then 'unavailable_input' when opn_e or cls_e then 'derived_E' else 'derived' end as value_state
        from m
        union all {rate('opening_rate', 'Opening rate', 'openings(t) / active(t-1)', 'opn', 'opn_ok', 'opn_e')}
        union all {rate('closure_rate', 'Closure rate', 'closures(t) / active(t-1)', 'cls', 'cls_ok', 'cls_e')}
        union all {rate('net_formation_rate', 'Net formation rate', '(openings(t) - closures(t)) / active(t-1)', 'opn - cls',
                        '(opn_ok and cls_ok)', '(opn_e or cls_e)')}""")
    metric = """case
        when industry_code_0722 = '1' and size_band_key = 'TOTAL' then case measure when 'active' then 'M01' when 'openings' then 'M02'
             when 'closures' then 'M03' when 'net_formation' then 'M04' when 'opening_rate' then 'M05' when 'closure_rate' then 'M06'
             when 'net_formation_rate' then 'M07' end
        when industry_code_0722 = '1' and size_band_key in ('1-4', '5-19') then case measure when 'active' then 'M09'
             when 'opening_rate' then 'M10' end
        when industry_code_0722 <> '1' and size_band_key = 'TOTAL' and measure <> 'continuing' then 'M11' end"""
    con.execute(f"""
        create or replace temp table fact_momentum as
        with u as (
            select p.cma_code_sgc2016, p.ref_month, p.size_id, p.industry_id, mm.measure, mm.measure_label, 'published' as value_origin,
                   cast(null as varchar) as formula, case when p.status_raw {ok_} then p.value_num end as value,
                   case p.status_raw when '' then 'published' when 'E' then 'published_E' when 'x' then 'suppressed_x'
                        when '..' then 'not_available_dotdot' else 'UNEXPECTED' end as value_state,
                   p.status_raw as status, p.vector
            from pub p join measure_map mm using (measure_id)
            union all
            select cma_code_sgc2016, ref_month, size_id, industry_id, measure, measure_label, 'derived', formula, value, value_state,
                   cast(null as varchar), cast(null as varchar)
            from der),
        f as (
            select '{SRC_0722}' as source_id, b.cma_code_sgc2016, b.cma_code_sgc2021, u.ref_month, s.size_band_key,
                   cast(u.industry_id as varchar) as industry_code_0722, u.measure, u.measure_label, u.value_origin, u.formula,
                   u.value, u.value_state in ('published', 'published_E', 'derived', 'derived_E') as value_available, u.value_state,
                   u.status, u.value_state in ('published_E', 'derived_E') as caution_flag,
                   case when u.measure in ('active', 'openings', 'continuing', 'closures') then 'enterprise'
                        when u.measure = 'net_formation' then 'enterprise (net count)' else 'ratio to active enterprises at t-1' end as statistical_unit,
                   case s.size_band_key when 'TOTAL' then 'all-employer-business context' when '1-4' then 'small-business measure'
                        when '5-19' then 'small-business measure' when '500-plus' then 'outside SME scope (500+)'
                        else 'SME-specific measure' end as scope_class,
                   '0722 monthly, seasonally adjusted' as period_label,
                   u.ref_month between '{PANDEMIC[0]}' and '{PANDEMIC[1]}' as pandemic_window_flag,
                   true as seasonally_adjusted, b.boundary_change_flag, u.vector
            from u join b_cma_df b using (cma_code_sgc2016) join size_map s using (size_id))
        select *, {metric} as metric_id from f""")
    return mvp


def build_1097(con, dim_cma, id2code, sector_ids, e5):
    sqljoin("1097_cell_grid_to_cache_by_cma_naics_size_codes", ["cma_code_sgc2021", "naics_id", "size_id"])
    sqljoin("1097_naics_id_to_dim_industry_code", ["naics_id"])
    sqljoin("1097_size_id_to_E5_band", ["size_id"])
    con.register("cma_grid", dim_cma[["cma_code_sgc2021"]])
    con.register("naics_codes", pd.DataFrame([(int(i), c) for i, c in id2code.items()], columns=["naics_id", "industry_code"]))
    con.register("e5", pd.DataFrame({"size_id": e5.source_code.astype(int), "size_band_key": e5.target_code}))
    con.execute(f"""
        create or replace temp table b as
        select right(DGUID, 3) as cma_code_sgc2021, naics_id, size_id, value_num
        from read_parquet('{CACHE_1097.as_posix()}') where DGUID like '2021S0503%'""")

    def cells(naics_ids, size_ids):
        # every in-scope (CMA, industry, size) cell; an omitted cell inside a CMA is zero by reconciliation (Phase B)
        return f"""select g.cma_code_sgc2021, n.naics_id, s.size_id, b.value_num, b.value_num is null as omitted
                   from cma_grid g cross join (select unnest([{','.join(map(str, naics_ids))}]) as naics_id) n
                   cross join (select unnest([{','.join(map(str, size_ids))}]) as size_id) s
                   left join b on b.cma_code_sgc2021 = g.cma_code_sgc2021 and b.naics_id = n.naics_id and b.size_id = s.size_id"""
    sme = [2, 3, 4, 5, 6, 7, 8]
    common = f"'{SRC_1097}' as source_id, 'location' as statistical_unit, '{PERIOD_1097}' as snapshot_period, '{REF_DATE_1097}' as source_ref_date"
    total_id = [int(i) for i, c in id2code.items() if c == "TOTAL"]
    con.execute(f"""
        create or replace temp table fact_scale as
        with c as ({cells(total_id, list(range(1, 10)))}),
        s as (select cma_code_sgc2021, 'all_sizes' as size_scope, size_id, value_num, omitted from c where size_id = 1
              union all select cma_code_sgc2021, 'sme_1_499', size_id, value_num, omitted from c where size_id between 2 and 8
              union all select cma_code_sgc2021, '500_plus', size_id, value_num, omitted from c where size_id = 9)
        select {common}, cma_code_sgc2021, size_scope,
               case size_scope when 'all_sizes' then 'S01' when 'sme_1_499' then 'S02' end as metric_id,
               case size_scope when 'all_sizes' then 'all-employer-business context' when 'sme_1_499' then 'SME-location measure'
                    else 'outside SME scope (500+)' end as scope_class,
               cast(sum(coalesce(value_num, 0)) as bigint) as locations,
               count(*) as n_component_cells, cast(sum(case when omitted then 0 else 1 end) as bigint) as n_published_cells,
               cast(sum(case when omitted then 1 else 0 end) as bigint) as n_zero_by_reconciliation_cells,
               bool_or(omitted) as omitted_zero_flag, string_agg(size_id::varchar, ';' order by size_id) as source_size_member_ids,
               'Total, all industries' as industry_scope
        from s group by all""")
    con.execute(f"""
        create or replace temp table fact_sector as
        with c as ({cells(sector_ids, [1] + sme)}),
        a as (select c.cma_code_sgc2021, n.industry_code as naics_sector,
                     cast(sum(case when size_id > 1 then coalesce(value_num, 0) end) as bigint) as sme_locations,
                     cast(sum(case when size_id = 1 then coalesce(value_num, 0) end) as bigint) as locations_all_sizes,
                     cast(sum(case when size_id > 1 and not omitted then 1 else 0 end) as bigint) as n_published_cells,
                     cast(sum(case when size_id > 1 and omitted then 1 else 0 end) as bigint) as n_zero_by_reconciliation_cells,
                     bool_or(size_id > 1 and omitted) as omitted_zero_flag
              from c join naics_codes n using (naics_id) group by all),
        d as (select *, naics_sector not in ('91', 'UNCLASSIFIED') as in_share_denominator,
                     sum(case when naics_sector not in ('91', 'UNCLASSIFIED') then sme_locations end) over (partition by cma_code_sgc2021) as classified_business_sme,
                     sum(sme_locations) over (partition by cma_code_sgc2021) as sme_total from a)
        select {common}, cma_code_sgc2021, naics_sector,
               case when naics_sector = 'UNCLASSIFIED' then 'unclassified_separate' when naics_sector = '91' then 'public_administration_separate'
                    else 'business_sector' end as sector_role,
               naics_sector = 'UNCLASSIFIED' as unclassified_separate, naics_sector = '91' as excluded_91_flag,
               'SME-location measure' as scope_class, '1-499' as size_scope, sme_locations, locations_all_sizes,
               case when in_share_denominator then 'S03' when naics_sector = 'UNCLASSIFIED' then 'S05' end as share_metric_id,
               case when in_share_denominator then sme_locations::double / classified_business_sme
                    when naics_sector = 'UNCLASSIFIED' then sme_locations::double / sme_total end as share,
               case when in_share_denominator then 'classified 1-499 locations in the 19 business sectors (excludes 91 and Unclassified)'
                    when naics_sector = 'UNCLASSIFIED' then 'all 1-499 locations (Unclassified share of SME locations)' end as share_denominator_basis,
               cast(case when in_share_denominator then classified_business_sme when naics_sector = 'UNCLASSIFIED' then sme_total end as bigint) as share_denominator,
               n_published_cells, n_zero_by_reconciliation_cells, omitted_zero_flag
        from d""")
    con.execute(f"""
        create or replace temp table fact_size as
        with c as ({cells(total_id, list(range(2, 10)))}),
        a as (select c.cma_code_sgc2021, e5.size_band_key, cast(sum(coalesce(value_num, 0)) as bigint) as locations,
                     cast(sum(case when omitted then 0 else 1 end) as bigint) as n_published_cells,
                     cast(sum(case when omitted then 1 else 0 end) as bigint) as n_zero_by_reconciliation_cells,
                     bool_or(omitted) as omitted_zero_flag, string_agg(size_id::varchar, ';' order by size_id) as source_size_member_ids
              from c join e5 using (size_id) group by all),
        d as (select *, sum(case when size_band_key <> '500-plus' then locations end) over (partition by cma_code_sgc2021) as sme_total from a)
        select {common}, cma_code_sgc2021, size_band_key, size_band_key <> '500-plus' as in_sme_scope,
               case when size_band_key = '500-plus' then 'outside SME scope (500+)' else 'SME-location measure' end as scope_class,
               locations, case when size_band_key <> '500-plus' then 'S04' end as share_metric_id,
               case when size_band_key <> '500-plus' then locations::double / sme_total end as share_of_sme_locations,
               cast(sme_total as bigint) as share_denominator, true as location_basis_flag,
               'location-based employment-size composition; not enterprise size' as unit_note,
               n_published_cells, n_zero_by_reconciliation_cells, omitted_zero_flag, source_size_member_ids, 'Total, all industries' as industry_scope
        from d""")


def ised_exclusion_reason(t, d, label):
    if t == "50":
        return TABLE_50_REASON
    if t not in ISED_SCOPE:
        return "table outside the MVP core tables"
    if d == "industry_naics_group" and label in ISED_AGG_CODES:
        return "industry special aggregate (E7; not in MVP)"
    if t == "5" and d in ISED_SEGMENT_DIMS:
        return "Table 5 outside national and size-band scope (I05)"
    return "segment dimension not in the MVP one-way grain"


def build_ised(con, e2, e4, e8, rkeys):
    c = con.execute(f"select * from read_parquet('{CACHE_ISED.as_posix()}')").df()
    defs = csv(VAL / "ised_2023" / "estimate_definitions.csv")[["measure_id", "block_title", "header_path", "unit", "universe_or_denominator"]]
    # Context-qualified lookup from the reviewed crosswalks: (dimension_class, verbatim ISED stub text) -> code.
    lk = pd.concat([
        pd.DataFrame({"dimension_class": "geography_cma", "label": e2.source_label,          # E2 key column (Phase E KEYCOL)
                       "segment_key": e2.target_code, "geography_caveat": e2.boundary_change_flag}),
        pd.DataFrame({"dimension_class": "geography_region", "label": list(rkeys), "segment_key": list(rkeys.values()),
                      "geography_caveat": "ISED region; never replicated to or split among provinces (E8)"}),
        pd.DataFrame({"dimension_class": "industry_naics_group", "label": e4.source_label,
                      "segment_key": [ised_code(s, l) for s, l in zip(e4.source_code, e4.source_label)], "geography_caveat": None}),
        pd.DataFrame({"dimension_class": "employment_size", "label": list(ISED_SIZE), "segment_key": list(ISED_SIZE.values()), "geography_caveat": None}),
        pd.DataFrame({"dimension_class": ["all_smes"], "label": [ISED_NATIONAL_LABEL], "segment_key": ["CANADA"], "geography_caveat": [None]}),
    ]).drop_duplicates(["dimension_class", "label"])
    for t in ("E2_cma", "E8_region", "E4_industry_group"):
        JOINS.append({"join": f"ised_stub_text_to_{t}", "left_keys": "dimension_class;label", "right_keys": "dimension_class;crosswalk key",
                      "kind": "ised_text_key"})
    keep = [t in ISED_SCOPE and d in ISED_SCOPE[t] and not (d == "industry_naics_group" and l in ISED_AGG_CODES)
            for t, d, l in zip(c.table_no, c.dimension_class, c.label)]
    inscope = c[keep].copy()
    m = inscope.merge(lk, on=["dimension_class", "label"], how="left", validate="many_to_one", indicator=True)
    m = kjoin("ised_measure_id_to_estimate_definitions", m, defs, ["measure_id"], validate="many_to_one")
    nested = {k: rid for rid, (s, k) in E7_LINKS.items() if s == "ISED_REGION"}
    seg_dim = m.dimension_class.map(ISED_SEGMENT_DIMS)
    q = m.quality.where(m.quality_state == "quality_code", None)
    use = []
    for vs, qq in zip(m.value_state, q):
        use.append("unavailable_dotdot" if vs == "dotdot" else "unavailable_blank" if vs == "blank" else
                   "excluded_quality_F" if qq == "F" else "usable_with_caution_E" if qq == "E" else
                   "usable_A_to_D" if isinstance(qq, str) else "published_without_quality_code")
    metric = {"ISED_T01_M001": "I01", "ISED_T03_M001": "I02", **{f"ISED_T03_M00{i}": "I03" for i in range(2, 8)}, "ISED_T49_M002": "I06"}
    unit = {"percentage_of_universe": "survey estimate (% of SMEs in segment)", "weighted_count": "survey estimate (weighted SME enterprises)",
            "unweighted_respondent_count": "survey respondents (unweighted count)", "percentage_share_of_total": "survey estimate (% share of weighted SMEs)",
            "weighted_total_monetary": "survey estimate (dollars, weighted total)", "average_monetary": "survey estimate (dollars, average)",
            "ratio_of_totals": "survey estimate (% ratio of weighted totals)"}
    region = seg_dim == "region"
    f = pd.DataFrame({
        "source_id": SRC_ISED, "survey_year": 2023, "period_label": "ISED SFGSME 2023 survey (calendar 2023)",
        "ised_table": m.table_no, "measure_id": m.measure_id, "measure_label": m.block_title + " | " + m.header_path,
        "estimate_type": m.estimate_type, "unit": m.unit, "universe_or_denominator": m.universe_or_denominator,
        "statistical_unit": m.estimate_type.map(unit),
        "scope_class": ["survey sample size (respondents)" if e == "unweighted_respondent_count" else "filtered SME survey measure" for e in m.estimate_type],
        "metric_id": [metric.get(i) or ("I04" if t == "4" else "I05" if t == "5" else None) for i, t in zip(m.measure_id, m.table_no)],
        "segment_dimension": seg_dim, "segment_key": m.segment_key, "segment_label_published": m.label, "segment_level": m.level,
        "nested_region_flag": region & m.segment_key.isin(list(nested)),
        "non_additive_rule_id": [nested.get(k) if r else None for k, r in zip(m.segment_key, region)],
        "geography_caveat": m.geography_caveat, "value_published": m.value.astype("Float64"), "value_state": m.value_state,
        "quality_code": q, "error_value": m.error.astype("Float64"), "se_or_cv": m.error_type,
        "caution_flag": q == "E", "excluded_quality_f": q == "F", "dotdot_flag": m.value_state == "dotdot", "mvp_use_status": use,
        "source_stub_row": m.stub_row})
    f["value_usable"] = f.value_published.where(f.mvp_use_status.isin(["usable_A_to_D", "usable_with_caution_E", "published_without_quality_code"]))
    reasons = pd.Series([ised_exclusion_reason(t, d, l) for t, d, l in zip(c.table_no, c.dimension_class, c.label)])
    excl = reasons[[not k for k in keep]].value_counts().rename_axis("reason").rename("rows").reset_index()
    return f, m, c, excl


# ---------------------------------------------------
# 4. Writing, validation, publishing
# ---------------------------------------------------

def write_view(con, name, df_or_table, pk, tmp):
    path = tmp / f"{name}.parquet"
    src = df_or_table
    if isinstance(df_or_table, pd.DataFrame):
        con.register("_v", df_or_table)
        src = "_v"
    con.execute(f"copy (select * from {src} order by {', '.join(pk)}) to '{path.as_posix()}' (format parquet, compression zstd)")
    if src == "_v":
        con.unregister("_v")
    return path


def pk_check(con, path, pk, view):
    rp = f"read_parquet('{path.as_posix()}')"
    required = [k for k in pk if k not in NULLABLE_PK_PARTS.get(view, set())]
    # grouping treats NULL parts as values, so declared nullable parts stay inside the uniqueness test
    dup = con.execute(f"select count(*) from (select {', '.join(pk)} from {rp} group by all having count(*) > 1)").fetchone()[0]
    nulls = con.execute(f"select count(*) from {rp} where " + " or ".join(f"{k} is null" for k in required)).fetchone()[0]
    rows = con.execute(f"select count(*) from {rp}").fetchone()[0]
    return dup == 0 and nulls == 0, rows, dup, nulls


def main():
    start = time.time()
    build_ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    con = duckdb.connect()
    protected_before = snapshot()
    manifest, shas, spec, cfacts = upstream(con)
    if not all(gates.values()):
        return finish(start, {}, published=False)
    e = {k: csv(XW / f) for k, f in XW_FILES.items()}
    e1, e2, e3, e4, e5, e7, e8 = e["E1"], e["E2"], e["E3"], e["E4"], e["E5"], e["E7"], e["E8"]
    rkeys = region_keys(e8)

    banner("2. DIMENSIONS AND BRIDGES")
    b_cma, b_ind, b_size = build_bridges(e1, e3, e4, e5)
    dim_cma, dguid_ok = build_dim_cma(e1, e2, e8, rkeys)
    dim_ind, e7_verified, id2code, sector_ids = build_dim_industry(e3, e4, e7, e8, rkeys)
    dim_size = build_dim_size(e5)
    print(f"  dim_cma {len(dim_cma)}, dim_industry {len(dim_ind)}, dim_size_band {len(dim_size)}; bridges {len(b_cma)}/{len(b_ind)}/{len(b_size)}")

    banner("3. FACTS")
    t0 = time.time()
    mvp_members = build_momentum(con, e3, b_cma)
    print(f"  momentum built in {time.time() - t0:.1f}s")
    build_1097(con, dim_cma, id2code, sector_ids, e5)
    tier1 = set(dim_cma[dim_cma.tier == 1].cma_code_sgc2021)
    tier3 = set(dim_cma[dim_cma.tier == 3].cma_code_sgc2021)
    ised, ised_m, ised_all, ised_excl = build_ised(con, e2, e4, e8, rkeys)

    pks = dict(zip(spec.view, [p.split(", ") for p in spec.primary_key]))
    tmp = OUT.parent / f".analytical_views_build_{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    views = {"dim_cma": dim_cma, "dim_industry": dim_ind, "dim_size_band": dim_size, "fact_cma_momentum_monthly": "fact_momentum",
             "fact_cma_market_scale_snapshot": "fact_scale", "fact_cma_sector_composition_snapshot": "fact_sector",
             "fact_cma_size_composition_snapshot": "fact_size", "fact_ised_financing_context": ised,
             "bridge_cma_vintage": b_cma, "bridge_industry": b_ind, "bridge_size_band": b_size}
    con.execute("set threads to 1")        # single-threaded writer: identical inputs give identical Parquet bytes
    paths = {v: write_view(con, v, views[v], pks[v], tmp) for v in VIEW_ORDER}
    con.execute("reset threads")
    rp = {v: f"read_parquet('{p.as_posix()}')" for v, p in paths.items()}

    def q(s):
        return con.execute(s).fetchone()

    def qdf(s):
        return con.execute(s).df()

    banner("4. VIEW VALIDATION")
    for v in VIEW_ORDER:
        ok, rows, dup, nulls = pk_check(con, paths[v], pks[v], v)
        gate(f"{v}__primary_key_unique_and_complete", ok, f"{rows:,} rows; {dup} duplicate keys; {nulls} null required key parts"
             + (f"; declared nullable: {sorted(NULLABLE_PK_PARTS[v])}" if v in NULLABLE_PK_PARTS else ""), v)
    sv = spec.set_index("view")
    for v in VIEW_ORDER:
        cols = {r[0] for r in con.execute(f"describe select * from {rp[v]}").fetchall()}
        need = {c.strip() for f in (sv.loc[v, "quality_and_scope_flags"], sv.loc[v, "period_fields"]) for c in f.split(",") if c.strip()}
        gate(f"{v}__has_spec_flag_and_period_columns", need <= cols,
             f"{len(need)} columns" if need <= cols else f"missing {sorted(need - cols)}", v)
    joins = pd.DataFrame(JOINS)
    cj = joins[joins.kind == "code"]
    code_keys = " ".join(cj.left_keys.tolist() + cj.right_keys.tolist()).lower()
    gate("no_join_uses_a_label_or_name", "label" not in code_keys and "name" not in code_keys and joins.kind.isin(["code", "ised_text_key"]).all(),
         f"{len(cj)} code-keyed joins")
    gate("ised_text_lookups_exact_context_qualified_and_declared",
         (joins.kind == "ised_text_key").sum() == 3 and (joins[joins.kind == "ised_text_key"].left_keys == "dimension_class;label").all()
         and (ised_m["_merge"] == "both").all(), f"{len(ised_m):,} in-scope ISED cells, each resolved exactly once")

    # dim_cma
    t = dim_cma.tier.value_counts().to_dict()
    gate("dim_cma__41_rows_unique_2021_code", len(dim_cma) == 41 and dim_cma.cma_code_sgc2021.is_unique, "", "dim_cma")
    gate("dim_cma__tiers_24_11_6", (t.get(1), t.get(2), t.get(3)) == (24, 11, 6), str(t), "dim_cma")
    gate("dim_cma__35_in_primary_decision_set", dim_cma.in_primary_decision_set.sum() == 35
         and set(dim_cma[dim_cma.in_primary_decision_set].tier) == {1, 2}, "", "dim_cma")
    gate("dim_cma__tier3_has_no_momentum_flag_or_2016_code", not dim_cma[dim_cma.tier == 3].has_momentum_0722.any()
         and dim_cma[dim_cma.tier == 3].cma_code_sgc2016.isna().all() and not dim_cma[dim_cma.tier == 3].momentum_comparison_eligible.any(), "", "dim_cma")
    gate("dim_cma__ised_cma_context_equals_tier1", set(dim_cma[dim_cma.has_ised_cma_context].cma_code_sgc2021) == tier1
         and set(e2.target_code) == tier1, "", "dim_cma")
    gate("dim_cma__dguids_agree_across_spine_E1_and_1097", dguid_ok, "", "dim_cma")
    gate("dim_cma__every_cma_has_province_region_key_and_description", dim_cma.ised_region_key.notna().all()
         and dim_cma.evidence_coverage_description.notna().all()
         and dim_cma.province_code.str.split(";").map(lambda ps: all(p in set(e8.target_code) for p in ps)).all(),
         f"multi-province: {dict(dim_cma[dim_cma.province_code.str.contains(';')][['cma_code_sgc2021', 'ised_region_key']].values.tolist())}", "dim_cma")
    # dim_industry
    cnt = dim_ind.industry_system.value_counts().to_dict()
    gate("dim_industry__systems_445_32_18", (cnt.get("NAICS2022_1097"), cnt.get("STATCAN_0722"), cnt.get("ISED_SFGSME_2023")) == (445, 32, 18),
         str(cnt), "dim_industry")
    nm = dim_ind[dim_ind.industry_system == "NAICS2022_1097"]
    gate("dim_industry__1097_mvp_is_19_sectors_plus_91_and_unclassified",
         (nm.mvp_role == "composition_sector").sum() == 19 and nm.in_mvp.sum() == 21
         and nm[nm.mvp_role == "composition_sector"].in_business_sector_scope.all()
         and not nm[nm.industry_code.isin(["91", "UNCLASSIFIED"])].in_business_sector_scope.any(), "", "dim_industry")
    gate("dim_industry__1097_parents_resolve", nm[nm.industry_level != "table_total"].parent_code.isin(set(nm.industry_code)).all(), "", "dim_industry")
    gate("dim_industry__0722_mvp_is_T004_plus_18_sectors", dim_ind[dim_ind.industry_system == "STATCAN_0722"].in_mvp.sum() == 19
         and len(mvp_members) == 19, "", "dim_industry")
    gate("dim_industry__ised_10_groups_in_mvp_8_aggregates_not", dim_ind[dim_ind.industry_system == "ISED_SFGSME_2023"].in_mvp.sum() == 10,
         "", "dim_industry")
    gate("dim_industry__overlapping_members_never_additive", not (dim_ind.overlap_flag & dim_ind.additive_flag).any(), "", "dim_industry")
    gate("dim_industry__all_28_E7_rules_linked_and_label_verified", len(e7_verified) == 28 and all(e7_verified.values())
         and dim_ind.non_additive_rule_id.notna().sum() == 23, f"{sum(e7_verified.values())} verified; 23 industry rules + 5 region rules", "dim_industry")
    ic = csv(VAL / "33100722" / "industry_classification.csv")
    gate("dim_industry__0722_member_ids_equal_phase_a_classification", set(ic.industry_id) == set(e3.source_member_id), "", "dim_industry")
    # dim_size_band
    gate("dim_size_band__5_bands_plus_total", len(dim_size) == 6 and dim_size.is_total.sum() == 1 and dim_size.in_sme_scope.sum() == 4,
         "", "dim_size_band")
    smeb = dim_size[dim_size.in_sme_scope].sort_values("sort_order")
    gate("dim_size_band__sme_bands_contiguous_1_to_499", smeb.lower_employees.tolist() == [1, 5, 20, 100]
         and smeb.upper_employees.tolist() == [4, 19, 99, 499], "", "dim_size_band")
    a0 = f"read_parquet('{CACHE_0722.as_posix()}')"
    lab0722 = dict(con.execute(f'select size_id, any_value("Employment size") from {a0} group by 1').fetchall())
    labm = dict(con.execute(f'select measure_id, any_value("Business dynamics measure") from {a0} group by 1').fetchall())
    gate("dim_size_band__0722_size_and_measure_maps_verified_against_member_labels",
         all(lab0722[i] == l for i, (_, l) in SIZE_0722.items()) and all(labm[i] == l for i, (_, l) in MEASURES_0722.items())
         and all(labm[i] == l for i, l in EXCLUDED_MEASURES_0722.items()), "", "dim_size_band")
    ised_sizes = set(ised_all[ised_all.dimension_class == "employment_size"].label)
    gate("dim_size_band__ised_size_labels_verified", ised_sizes == set(ISED_SIZE), str(sorted(ised_sizes)), "dim_size_band")
    gate("dim_size_band__carries_no_statistical_unit", "statistical_unit" not in dim_size.columns, "", "dim_size_band")
    # bridges
    gate("bridge_cma_vintage__35_plus_6_target_only", len(b_cma) == 41 and b_cma.cma_code_sgc2016.isna().sum() == 6
         and set(b_cma[b_cma.cma_code_sgc2016.isna()].cma_code_sgc2021) == tier3 and not b_cma.exact_boundary_claimed.any(), "", "bridge_cma_vintage")
    gate("bridge_industry__40_plus_23_rows_no_allocation", len(b_ind) == 63 and (b_ind.source_system == "STATCAN_0722").sum() == 40
         and not b_ind.value_allocation_allowed.any(), "", "bridge_industry")
    gate("bridge_size_band__8_bands_plus_total", len(b_size) == 9 and (b_size.target_band == "TOTAL").sum() == 1
         and set(b_size.target_band) == set(dim_size.size_band_key), "", "bridge_size_band")

    # fact_cma_momentum_monthly
    fm = rp["fact_cma_momentum_monthly"]
    ids = ",".join(map(str, mvp_members))
    cma_status = dict(con.execute(f"select coalesce(STATUS, ''), count(*) from {a0} where DGUID like '2016S0503%' group by 1").fetchall())
    total_cma = sum(cma_status.values())
    inc_status = dict(con.execute(f"select coalesce(STATUS, ''), count(*) from {a0} where DGUID like '2016S0503%' and measure_id <= 4 and industry_id in ({ids}) group by 1").fetchall())
    inc = sum(inc_status.values())
    exm = q(f"select count(*), count(value_num) from {a0} where DGUID like '2016S0503%' and measure_id > 4")
    exi = q(f"select count(*), count(value_num) from {a0} where DGUID like '2016S0503%' and measure_id <= 4 and industry_id not in ({ids})")
    pubn, dern = q(f"select count(*) filter (where value_origin = 'published'), count(*) filter (where value_origin = 'derived') from {fm}")
    gate("momentum__scope_reconciles_to_all_cma_rows_in_cache", total_cma == inc + exm[0] + exi[0] and pubn == inc,
         f"{total_cma:,} CMA rows = {inc:,} included + {exm[0]:,} excluded measures + {exi[0]:,} excluded industries", "fact_cma_momentum_monthly")
    gate("momentum__excluded_measures_have_no_cma_values", exm[1] == 0, "reopening, entrants, temporary closures, exits", "fact_cma_momentum_monthly")
    gate("momentum__35_cmas_all_in_bridge_none_tier3", q(f"select count(distinct cma_code_sgc2016) from {fm}")[0] == 35
         and q(f"select count(*) from {fm} f anti join {rp['bridge_cma_vintage']} b using (cma_code_sgc2016, cma_code_sgc2021)")[0] == 0
         and q(f"select count(*) from {fm} where cma_code_sgc2021 in ({','.join(repr(c) for c in sorted(tier3))})")[0] == 0,
         "", "fact_cma_momentum_monthly")
    gate("momentum__foreign_keys_resolve_to_dim_industry_and_dim_size_band",
         q(f"select count(*) from {fm} f anti join {rp['dim_industry']} d on d.industry_system = 'STATCAN_0722' and d.industry_code = f.industry_code_0722")[0] == 0
         and q(f"select count(*) from {fm} f anti join {rp['dim_size_band']} d using (size_band_key)")[0] == 0
         and q(f"select count(distinct industry_code_0722), count(distinct size_band_key) from {fm}") == (19, 6), "19 members; 6 size scopes",
         "fact_cma_momentum_monthly")
    size_case = " ".join(f"when '{k}' then {i}" for i, (k, _) in SIZE_0722.items())
    meas_case = " ".join(f"when '{k}' then {i}" for i, (k, _) in MEASURES_0722.items())
    matched, mism = q(f"""select count(*), count(*) filter (where f.value is distinct from (case when coalesce(a.STATUS, '') in ('', 'E') then a.value_num end)
                                                        or f.status is distinct from coalesce(a.STATUS, '') or f.vector is distinct from a.VECTOR)
                          from {fm} f join {a0} a
                            on a.DGUID = '2016S0503' || f.cma_code_sgc2016 and a.REF_DATE = f.ref_month
                           and a.industry_id = cast(f.industry_code_0722 as int) and a.size_id = (case f.size_band_key {size_case} end)
                           and a.measure_id = (case f.measure {meas_case} end)
                          where f.value_origin = 'published'""")
    gate("momentum__published_values_status_and_vector_equal_cache", matched == pubn and mism == 0,
         f"{matched:,} rows matched; {mism} mismatches", "fact_cma_momentum_monthly")
    st = dict(con.execute(f"select value_state, count(*) from {fm} where value_origin = 'published' group by 1").fetchall())
    gate("momentum__unavailable_preserved_never_zero_filled",
         st.get("suppressed_x", 0) == inc_status.get("x", 0) and st.get("not_available_dotdot", 0) == inc_status.get("..", 0) and "UNEXPECTED" not in st
         and q(f"select count(*) from {fm} where (value is null) = value_available")[0] == 0, str(st), "fact_cma_momentum_monthly")
    # independent recomputation of the derived core metrics (M04-M07): business-sector total, all sizes
    ind_df = qdf(f"""select right(DGUID, 3) cma, REF_DATE m, measure_id, case when coalesce(STATUS, '') in ('', 'E') then value_num end v
                     from {a0} where DGUID like '2016S0503%' and industry_id = 1 and size_id = 1 and measure_id in (1, 2, 4)""")
    pv = ind_df.pivot(index=["cma", "m"], columns="measure_id", values="v")
    present = set(map(tuple, ind_df[ind_df.measure_id.isin([2, 4])][["cma", "m"]].drop_duplicates().values.tolist()))
    pv = pv.reset_index()
    pv["prev"] = (pd.PeriodIndex(pv.m, freq="M") - 1).astype(str)
    pv = pv.merge(pv[["cma", "m", 1]].rename(columns={"m": "prev", 1: "pact"}), on=["cma", "prev"], how="left")
    pv["net_formation"] = pv[2] - pv[4]
    pv["opening_rate"] = pv[2] / pv.pact
    pv["closure_rate"] = pv[4] / pv.pact
    pv["net_formation_rate"] = (pv[2] - pv[4]) / pv.pact
    pv = pv[[k in present for k in zip(pv.cma, pv.m)]]
    exp = pv.melt(id_vars=["cma", "m"], value_vars=["net_formation", "opening_rate", "closure_rate", "net_formation_rate"], var_name="measure", value_name="exp")
    got = qdf(f"select cma_code_sgc2016 cma, ref_month m, measure, value from {fm} where industry_code_0722 = '1' and size_band_key = 'TOTAL' and value_origin = 'derived'")
    cmpd = exp.merge(got, on=["cma", "m", "measure"], how="outer", indicator=True)
    bad = int(((cmpd._merge != "both") | (cmpd.exp.isna() != cmpd.value.isna()) | ((cmpd.exp - cmpd.value).abs() > 1e-12)).sum())
    gate("momentum__derived_M04_M07_match_independent_recomputation", bad == 0, f"{len(cmpd):,} derived values; {bad} differences", "fact_cma_momentum_monthly")
    latest = q(f"select max(ref_month) from {fm} where metric_id = 'M01' and value_available")[0]
    gate("momentum__latest_month_2026_05_and_first_month_has_no_rate", latest == "2026-05"
         and q(f"select count(*) from {fm} where ref_month = '2015-01' and measure like '%rate' and value_state <> 'no_prior_month'")[0] == 0,
         latest, "fact_cma_momentum_monthly")
    gate("momentum__derived_rows_carry_own_unit_and_formula",
         q(f"select count(*) from {fm} where value_origin = 'derived' and (formula is null or statistical_unit = 'enterprise')")[0] == 0,
         f"{dern:,} derived rows", "fact_cma_momentum_monthly")
    gate("momentum__all_size_series_labelled_all_employer_context",
         q(f"select count(*) from {fm} where size_band_key = 'TOTAL' and scope_class <> 'all-employer-business context'")[0] == 0
         and q(f"select count(*) from {fm} where size_band_key = '500-plus' and scope_class <> 'outside SME scope (500+)'")[0] == 0, "", "fact_cma_momentum_monthly")

    # 1097 facts
    fs, fsec, fsz = rp["fact_cma_market_scale_snapshot"], rp["fact_cma_sector_composition_snapshot"], rp["fact_cma_size_composition_snapshot"]
    esp = csv(VAL / "33101097" / "employment_size_profile.csv").set_index("size_id")
    tot41, unc41 = int(esp.loc["1", "locations_all_41_cmas"]), int(esp.loc["1", "unclassified_all_41_cmas"])
    piv = f"(pivot {fs} on size_scope using sum(locations) group by cma_code_sgc2021)"
    gate("scale__123_rows_41_cmas_3_scopes", q(f"select count(*), count(distinct cma_code_sgc2021) from {fs}") == (123, 41), "", "fact_cma_market_scale_snapshot")
    gate("scale__all_sizes_is_the_published_top_cell_for_every_cma",
         q(f"select count(*) from {fs} where size_scope = 'all_sizes' and n_published_cells = 1")[0] == 41, "", "fact_cma_market_scale_snapshot")
    d1 = q(f'select count(*) from {piv} where all_sizes <> sme_1_499 + "500_plus"')[0]
    gate("scale__sme_equals_total_minus_500_plus_exactly", d1 == 0, f"{d1} CMAs differ", "fact_cma_market_scale_snapshot")
    s_all, s_sme = q(f"select sum(locations) filter (where size_scope = 'all_sizes'), sum(locations) filter (where size_scope = 'sme_1_499') from {fs}")
    gate("scale__41_cma_total_reproduces_phase_b", s_all == tot41, f"{s_all:,} vs {tot41:,}", "fact_cma_market_scale_snapshot")
    gate("sector__861_rows_full_grid", q(f"select count(*), count(distinct cma_code_sgc2021), count(distinct naics_sector) from {fsec}") == (861, 41, 21),
         "41 CMAs x (19 sectors + 91 + Unclassified)", "fact_cma_sector_composition_snapshot")
    d2 = q(f"""select count(*) from (select cma_code_sgc2021, sum(sme_locations) s from {fsec} group by 1) a
               join {fs} b using (cma_code_sgc2021) where b.size_scope = 'sme_1_499' and a.s <> b.locations""")[0]
    gate("sector__sectors_plus_91_plus_unclassified_equal_sme_total", d2 == 0, f"{d2} CMAs differ", "fact_cma_sector_composition_snapshot")
    sh = q(f"select max(abs(s - 1)) from (select sum(share) s from {fsec} where share_metric_id = 'S03' group by cma_code_sgc2021)")[0]
    gate("sector__19_sector_shares_sum_to_one", sh < 1e-12, f"max deviation {sh:.1e}", "fact_cma_sector_composition_snapshot")
    ua = q(f"select sum(locations_all_sizes) filter (where naics_sector = 'UNCLASSIFIED'), sum(locations_all_sizes) from {fsec}")
    gate("sector__unclassified_and_total_reproduce_phase_b", ua == (unc41, tot41), f"Unclassified {ua[0]:,}; all {ua[1]:,}", "fact_cma_sector_composition_snapshot")
    gate("sector__unclassified_and_91_never_in_share_denominator_and_codes_in_dim_industry",
         q(f"select count(*) from {fsec} where naics_sector in ('91', 'UNCLASSIFIED') and share_metric_id = 'S03'")[0] == 0
         and q(f"select count(*) from {fsec} f anti join {rp['dim_industry']} d on d.industry_system = 'NAICS2022_1097' and d.industry_code = f.naics_sector")[0] == 0,
         "", "fact_cma_sector_composition_snapshot")
    gate("size__205_rows_full_grid", q(f"select count(*), count(distinct cma_code_sgc2021) from {fsz}") == (205, 41), "", "fact_cma_size_composition_snapshot")
    d3 = q(f"""select count(*) from (select cma_code_sgc2021, sum(locations) filter (where in_sme_scope) s, sum(locations) a from {fsz} group by 1) x
               join {piv} y using (cma_code_sgc2021) where x.s <> y.sme_1_499 or x.a <> y.all_sizes""")[0]
    gate("size__bands_reconcile_to_scale_snapshot", d3 == 0, f"{d3} CMAs differ", "fact_cma_size_composition_snapshot")
    sh2 = q(f"select max(abs(s - 1)) from (select sum(share_of_sme_locations) s from {fsz} group by cma_code_sgc2021)")[0]
    gate("size__sme_band_shares_sum_to_one_500_plus_outside", sh2 < 1e-12
         and q(f"select count(*) from {fsz} where size_band_key = '500-plus' and share_of_sme_locations is not null")[0] == 0,
         f"max deviation {sh2:.1e}", "fact_cma_size_composition_snapshot")
    for v in ("fact_cma_market_scale_snapshot", "fact_cma_sector_composition_snapshot", "fact_cma_size_composition_snapshot"):
        gate(f"{v}__foreign_keys_resolve_to_dims",
             q(f"select count(*) from {rp[v]} f anti join {rp['dim_cma']} d using (cma_code_sgc2021)")[0] == 0
             and (v != "fact_cma_size_composition_snapshot" or q(f"select count(*) from {rp[v]} f anti join {rp['dim_size_band']} d using (size_band_key)")[0] == 0), "", v)
    zero_rows = sum(q(f"select count(*) from {rp[v]} where omitted_zero_flag")[0] for v in
                    ("fact_cma_market_scale_snapshot", "fact_cma_sector_composition_snapshot", "fact_cma_size_composition_snapshot"))
    gate("1097__zeros_come_only_from_omitted_in_scope_cells",
         q(f"select count(*) from {fs} where n_published_cells + n_zero_by_reconciliation_cells <> n_component_cells")[0] == 0
         and q(f"select count(*) from read_parquet('{CACHE_1097.as_posix()}') where value_num = 0")[0] == 0,
         f"{zero_rows} rows include at least one zero-by-reconciliation cell; no published zeros", "general")

    # fact_ised_financing_context
    fi = rp["fact_ised_financing_context"]
    # exact expectations: measures per table x segments per dimension, with segment counts anchored to Phase C (not to the lookups)
    n_meas = ised_all.groupby("table_no").measure_id.nunique().to_dict()
    seg_n = {"national": 1, "size_band": cfacts["size_bands"], "cma": cfacts["cmas"], "region": len(set(e8.source_label)),
             "industry_group": cfacts["industry_groups"]}
    exp_dim = {}
    for t_, dims in ISED_SCOPE.items():
        for d_ in dims:
            exp_dim[ISED_SEGMENT_DIMS[d_]] = exp_dim.get(ISED_SEGMENT_DIMS[d_], 0) + n_meas[t_] * seg_n[ISED_SEGMENT_DIMS[d_]]
    got_dim = ised.segment_dimension.value_counts().to_dict()
    got_keys = ised.dropna(subset=["segment_key"]).groupby("segment_dimension").segment_key.nunique().to_dict()
    gate("ised__rows_and_distinct_keys_per_dimension_are_exact", got_dim == exp_dim and got_keys == seg_n and ised.segment_key.notna().all()
         and all(v > 0 for v in exp_dim.values()), f"rows {got_dim}; keys {got_keys}", "fact_ised_financing_context")
    gate("ised__in_scope_plus_excluded_reconcile_to_cache", int(ised_excl.rows.sum()) + len(ised) == len(ised_all),
         f"{len(ised):,} in scope + {int(ised_excl.rows.sum()):,} excluded = {len(ised_all):,}", "fact_ised_financing_context")
    gate("ised__cma_segments_are_the_24_tier1_codes_table5_national_size_only", set(ised[ised.segment_dimension == "cma"].segment_key) == tier1
         and q(f"select count(*) from {fi} where ised_table = '5' and segment_dimension not in ('national', 'size_band')")[0] == 0,
         "", "fact_ised_financing_context")
    gate("ised__quebec_province_row_never_resolves_to_cma",
         set(ised[(ised.segment_dimension == "region") & (ised.segment_label_published == "Quebec")].segment_key) == {"24"}
         and set(ised[(ised.segment_dimension == "cma") & (ised.segment_label_published == "Quebec")].segment_key) == {"421"}
         and not ised[ised.segment_dimension == "region"].segment_key.isin(tier1).any(), "", "fact_ised_financing_context")
    gate("ised__segment_keys_resolve_to_dimensions",
         ised[ised.segment_dimension == "size_band"].segment_key.isin(dim_size.size_band_key).all()
         and ised[ised.segment_dimension == "industry_group"].segment_key.isin(dim_ind[dim_ind.industry_system == "ISED_SFGSME_2023"].industry_code).all()
         and ised[ised.segment_dimension == "region"].segment_key.isin(list(rkeys.values())).all()
         and ised[ised.segment_dimension == "cma"].segment_key.isin(dim_cma.cma_code_sgc2021).all(), "", "fact_ised_financing_context")
    mi = q(f"""select count(*), count(*) filter (where f.value_published is distinct from c.value or f.quality_code is distinct from
                      (case when c.quality_state = 'quality_code' then c.quality end) or f.value_state <> c.value_state)
               from {fi} f join read_parquet('{CACHE_ISED.as_posix()}') c on c.measure_id = f.measure_id and c.stub_row = f.source_stub_row""")
    gate("ised__published_values_and_quality_equal_cache", mi == (len(ised), 0), f"{mi[0]:,} matched; {mi[1]} mismatches", "fact_ised_financing_context")
    gate("ised__F_excluded_dotdot_never_zero", q(f"select count(*) from {fi} where (excluded_quality_f or dotdot_flag) and value_usable is not null")[0] == 0
         and q(f"select count(*) from {fi} where dotdot_flag and value_published is not null")[0] == 0, "", "fact_ised_financing_context")
    nat = q(f"""select max(value_published) filter (where measure_id = 'ISED_T49_M002'), max(value_published) filter (where measure_id = 'ISED_T49_M001')
                from {fi} where segment_key = 'CANADA'""")
    gate("ised__national_weighted_smes_and_respondents_reproduce_phase_c", nat == (cfacts["weighted_smes"], cfacts["respondents"]),
         f"{nat[0]:,.0f} weighted SMEs; {nat[1]:,.0f} respondents", "fact_ised_financing_context")
    reg = ised[ised.segment_dimension == "region"]
    gate("ised__nested_region_rows_flagged_with_E7_rule", (reg.nested_region_flag == reg.segment_key.isin(["35-N", "35-S", "46", "47", "48"])).all()
         and int(reg.nested_region_flag.sum()) == 5 * reg.measure_id.nunique() > 0
         and reg[reg.nested_region_flag].non_additive_rule_id.notna().all() and not ised[ised.segment_dimension != "region"].nested_region_flag.any(),
         f"{int(reg.nested_region_flag.sum())} nested rows", "fact_ised_financing_context")

    # every fact view: one source, and unit, period and scope on every row; no NaN stored
    for v, pcol in FACT_PERIOD.items():
        n_src, n_missing = q(f"select count(distinct source_id), count(*) filter (where source_id is null or statistical_unit is null or {pcol} is null or scope_class is null) from {rp[v]}")
        gate(f"{v}__single_source_with_unit_period_scope_on_every_row", n_src == 1 and n_missing == 0, f"{n_missing} incomplete rows", v)
    nan = 0
    for v in VIEW_ORDER:
        cols = [r[0] for r in con.execute(f"describe select * from {rp[v]}").fetchall() if r[1] == "DOUBLE"]
        if cols:
            nan += q(f"select count(*) from {rp[v]} where " + " or ".join(f"isnan({c})" for c in cols))[0]
    gate("no_nan_stored_in_any_view", nan == 0, f"{nan} NaN values")

    data_ok = all(gates.values())
    src_ids = {"dim_cma": f"{SRC_1097} (geography profile); E1; E2; E8; Phase F spine", "dim_industry": f"{SRC_1097} (NAICS profile); E3; E4; E7",
               "dim_size_band": "E5; 0722 and ISED size members", "fact_cma_momentum_monthly": SRC_0722,
               "fact_cma_market_scale_snapshot": SRC_1097, "fact_cma_sector_composition_snapshot": SRC_1097,
               "fact_cma_size_composition_snapshot": SRC_1097, "fact_ised_financing_context": SRC_ISED,
               "bridge_cma_vintage": "E1", "bridge_industry": "E3; E4", "bridge_size_band": "E5"}
    snap = f"{PERIOD_1097} (REF_DATE {REF_DATE_1097})"
    periods = {"fact_cma_momentum_monthly": "2015-01 to 2026-05 (monthly, seasonally adjusted)", "fact_cma_market_scale_snapshot": snap,
               "fact_cma_sector_composition_snapshot": snap, "fact_cma_size_composition_snapshot": snap, "fact_ised_financing_context": "2023 survey"}
    script_sha = sha256_file(Path(__file__).resolve())
    man = []
    for v in VIEW_ORDER:
        schema = con.execute(f"describe select * from {rp[v]}").fetchall()
        man.append({"view_name": v, "output_path": f"data/processed/analytical_views/{v}.parquet", "build_timestamp_utc": build_ts,
                    "row_count": q(f"select count(*) from {rp[v]}")[0], "column_count": len(schema), "primary_key": ", ".join(pks[v]),
                    "source_ids": src_ids[v], "source_periods": periods.get(v, "not applicable (classification)"),
                    "statistical_unit": spec.set_index("view").loc[v, "statistical_unit"], "file_sha256": sha256_file(paths[v]),
                    "schema_fingerprint": hashlib.sha256(";".join(f"{r[0]}:{r[1]}" for r in schema).encode()).hexdigest()[:16],
                    "build_script_version": f"{BUILD_VERSION} (script sha256 {script_sha[:12]})",
                    "validation_status": "passed" if data_ok else "failed"})
    man = pd.DataFrame(man)
    man.to_csv(tmp / "analytical_views_manifest.csv", index=False, encoding="utf-8", lineterminator="\n")

    scope = pd.DataFrame([
        {"source": SRC_0722, "rows": total_cma, "included": "all", "treatment": "CMA rows in the lineage-checked cache"},
        {"source": SRC_0722, "rows": inc, "included": "yes", "treatment": "T004 + 18 sector members x 6 size scopes x 4 published measures"},
        {"source": SRC_0722, "rows": exm[0], "included": "no", "treatment": f"reopening, entrants, temporary closures, exits ({exm[1]:,} valued at CMA level)"},
        {"source": SRC_0722, "rows": exi[0], "included": "no", "treatment": f"tourism members, subsectors 311/312, member 32 aggregate ({exi[1]:,} valued)"},
        {"source": SRC_ISED, "rows": len(ised_all), "included": "all", "treatment": "value cells in the ISED cache"},
        {"source": SRC_ISED, "rows": len(ised), "included": "yes",
         "treatment": "Tables 1, 3, 4, 49 (national, 4 size bands, 24 CMAs, 10 regions, 10 groups); Table 5 (national, 4 size bands)"},
        *[{"source": SRC_ISED, "rows": int(r.rows), "included": "no", "treatment": r.reason} for r in ised_excl.itertuples()],
    ])
    states = pd.concat(
        [qdf(f"select 'fact_cma_momentum_monthly' as view, value_origin || ':' || value_state as value_state, count(*) as rows from {fm} group by all"),
         qdf(f"select 'fact_ised_financing_context' as view, mvp_use_status as value_state, count(*) as rows from {fi} group by all")]
        + [qdf(f"select '{v}' as view, case when omitted_zero_flag then 'includes_zero_by_reconciliation' else 'all_components_published' end as value_state, count(*) as rows from {rp[v]} group by all")
           for v in ("fact_cma_market_scale_snapshot", "fact_cma_sector_composition_snapshot", "fact_cma_size_composition_snapshot")]
    ).sort_values(["view", "value_state"])
    t50 = int(ised_excl[ised_excl.reason == TABLE_50_REASON].rows.sum())
    dev = pd.DataFrame([
        {"item": "ISED Table 50 excluded from fact_ised_financing_context", "phase_f_reference": "metric I06 cites Tables 49/50",
         "decision": "I06 is served by Table 49 (weighted SMEs by one-way segment); Table 50 is a region x industry cross and would break the one-way grain",
         "rows_affected": t50},
        {"item": "Derived momentum measures stored as rows", "phase_f_reference": "M04-M07, M10, M11 formulas",
         "decision": "value_origin = derived, own statistical_unit and formula; computed only from available same-series inputs; t-1 is the previous calendar month",
         "rows_affected": dern},
        {"item": "ISED stub text looked up in reviewed crosswalks", "phase_f_reference": "no name joins",
         "decision": "ISED publishes no codes; the verbatim stub text is matched exactly, within its row context, to the E2/E4/E8 keys and only codes are used downstream",
         "rows_affected": len(ised)},
        {"item": "Momentum band scope classes", "phase_f_reference": "metric scope classes",
         "decision": "500+ labelled 'outside SME scope (500+)'; 20-99 and 100-499 labelled 'SME-specific measure' (active only; 18 CMAs publish them)",
         "rows_affected": q(f"select count(*) from {fm} where size_band_key in ('20-99', '100-499', '500-plus')")[0]},
        {"item": "Declared nullable key parts", "phase_f_reference": "primary keys",
         "decision": "bridge_cma_vintage.cma_code_sgc2016 is NULL for the 6 target-only CMAs; bridge_industry.target_code is NULL for aggregates and non-reconcilable members",
         "rows_affected": int(b_cma.cma_code_sgc2016.isna().sum() + b_ind.target_code.isna().sum())},
    ])
    for df, name in ((joins, "join_register.csv"), (scope, "scope_reconciliation.csv"), (states, "value_state_counts.csv"),
                     (dev, "declared_deviations.csv")):
        df.to_csv(tmp / f"_val_{name}", index=False, encoding="utf-8", lineterminator="\n")

    published = False
    if data_ok:
        banner("5. PUBLISH")
        OUT.mkdir(parents=True, exist_ok=True)
        for v in VIEW_ORDER:
            os.replace(paths[v], OUT / f"{v}.parquet")
        os.replace(tmp / "analytical_views_manifest.csv", OUT / "analytical_views_manifest.csv")
        published = True
        gate("published_files_match_manifest_checksums", all(sha256_file(ROOT / r.output_path) == r.file_sha256 for r in man.itertuples()),
             f"{len(man)} views")
    else:
        print("  data gates failed: views NOT published; existing outputs left untouched")
    VOUT.mkdir(parents=True, exist_ok=True)
    for f in tmp.glob("_val_*"):
        os.replace(f, VOUT / f.name[5:])
    shutil.rmtree(tmp, ignore_errors=True)

    use = ised.mvp_use_status.value_counts().to_dict()
    facts = {
        "views": len(VIEW_ORDER), "fact_views": len(FACT_PERIOD), "dim_cma_rows": len(dim_cma), "dim_industry_rows": len(dim_ind),
        "dim_size_band_rows": len(dim_size), "momentum_rows": pubn + dern, "momentum_published_rows": pubn, "momentum_derived_rows": dern,
        "momentum_cma_cache_rows": total_cma, "momentum_published_available": st.get("published", 0) + st.get("published_E", 0),
        "momentum_published_E": st.get("published_E", 0), "momentum_suppressed_x": st.get("suppressed_x", 0),
        "momentum_dotdot": st.get("not_available_dotdot", 0), "momentum_excluded_measure_rows": exm[0], "momentum_excluded_industry_rows": exi[0],
        "locations_41": tot41, "sme_locations_41": s_sme, "unclassified_41": unc41, "zero_by_rec_rows": zero_rows,
        "ised_rows": len(ised), "ised_cells": len(ised_all), "ised_excluded_rows": int(ised_excl.rows.sum()), "ised_t50_cells": t50,
        "ised_usable_A_to_D": use.get("usable_A_to_D", 0), "ised_caution_E": use.get("usable_with_caution_E", 0),
        "ised_excluded_F": use.get("excluded_quality_F", 0), "ised_dotdot": use.get("unavailable_dotdot", 0),
        "ised_no_quality_code": use.get("published_without_quality_code", 0),
        "weighted_smes": cfacts["weighted_smes"], "respondents": cfacts["respondents"], "t05_cma_pct_F": cfacts["t05_cma_pct_F"],
        "tier1": t.get(1), "tier2": t.get(2), "tier3": t.get(3), "primary": int(dim_cma.in_primary_decision_set.sum()),
        "ind_1097": cnt.get("NAICS2022_1097"), "ind_0722": cnt.get("STATCAN_0722"), "ind_ised": cnt.get("ISED_SFGSME_2023"),
        "ised_seg_keys": got_keys, "recompute_values": len(cmpd), "recompute_cmas": int(cmpd.cma.nunique()), "raw_files": len(manifest),
        "manifest": man[["view_name", "row_count", "column_count", "file_sha256", "schema_fingerprint"]].to_dict("records")}
    finish_docs(facts)
    protected_after = snapshot()
    changed = sorted(k for k in set(protected_before) | set(protected_after) if protected_before.get(k) != protected_after.get(k))
    gate("no_protected_input_changed", not changed, f"{len(protected_after)} files (caches, crosswalks, Phase A-F outputs)" + (f"; changed {changed}" if changed else ""))
    raw_changed = [r.relative_path for r in manifest.itertuples() if sha256_file(ROOT / r.relative_path) != shas[r.relative_path]]
    gate("no_raw_file_changed", not raw_changed, f"{len(manifest)} files")
    pd.DataFrame(checks).to_csv(VOUT / "view_checks.csv", index=False, encoding="utf-8", lineterminator="\n")
    con.close()
    finish(start, {"facts": facts, "doc_claims": DOC_CLAIMS, "published": published}, published)


def finish_docs(facts):
    banner("6. DOCUMENTATION")
    F = {k: (f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)) for k, v in facts.items()}
    DOC_CLAIMS.update({
        "views": f"**{F['views']}** views",
        "momentum_rows": f"**{F['momentum_rows']}** rows",
        "momentum_published": f"**{F['momentum_published_rows']}** published",
        "momentum_derived": f"**{F['momentum_derived_rows']}** derived",
        "cma_cache": f"**{F['momentum_cma_cache_rows']}** CMA rows",
        "available": f"**{F['momentum_published_available']}** carry a value",
        "x": f"**{F['momentum_suppressed_x']}** are suppressed (`x`)",
        "dotdot": f"**{F['momentum_dotdot']}** are not available (`..`)",
        "locations": f"**{F['locations_41']}** employer locations",
        "sme_locations": f"**{F['sme_locations_41']}** SME locations",
        "unclassified": f"**{F['unclassified_41']}** Unclassified",
        "zero_rows": f"**{F['zero_by_rec_rows']}** snapshot rows",
        "ised_rows": f"**{F['ised_rows']}** ISED rows",
        "ised_F": f"**{F['ised_excluded_F']}** F estimates",
        "t50": f"**{F['ised_t50_cells']}** Table 50 cells",
        "weighted": f"**{F['weighted_smes']}** weighted SMEs",
        "respondents": f"**{F['respondents']}** respondents",
        "dim_industry": f"**{F['dim_industry_rows']}** industry members",
        "published_E": f"{F['momentum_published_E']} flagged `E`",
        "tiers": f"{F['tier1']}, {F['tier2']} and {F['tier3']} CMAs in Tiers 1, 2 and 3; {F['primary']} in the primary decision set",
        "ind_systems": f"{F['ind_1097']} NAICS 2022 members from 1097, {F['ind_0722']} 0722 members, and {F['ind_ised']} ISED groups and aggregates",
        "ised_segments": (f"national, the {facts['ised_seg_keys']['size_band']} size bands, the {facts['ised_seg_keys']['cma']} Tier 1 CMAs, "
                          f"{facts['ised_seg_keys']['region']} regions and provinces, and the {facts['ised_seg_keys']['industry_group']} industry groups"),
        "t05": f"at CMA level it is {F['t05_cma_pct_F']}% F",
        "raw": f"all {F['raw_files']} raw files",
        "recompute": f"in all {F['recompute_cmas']} CMAs ({F['recompute_values']} values), matches exactly",
    })
    # section 1 table: the line for each view must state its manifest row count
    for r in facts["manifest"]:
        line = next((ln for ln in (DOC04.read_text(encoding="utf-8").splitlines() if DOC04.exists() else []) if ln.startswith(f"| `{r['view_name']}` |")), "")
        DOC_CLAIMS[f"table_{r['view_name']}"] = f"| {r['row_count']:,} |" if f"| {r['row_count']:,} |" in line else f"<row count {r['row_count']:,} missing from table line>"
    doc = DOC04.read_text(encoding="utf-8") if DOC04.exists() else ""
    missing = {k: v for k, v in DOC_CLAIMS.items() if v not in doc}
    gate("documentation_numbers_reproduced_by_outputs", bool(doc) and not missing,
         ("NOT YET DOCUMENTED" if not doc else f"{len(DOC_CLAIMS) - len(missing)} of {len(DOC_CLAIMS)} claims") + (f"; missing {missing}" if doc and missing else ""))
    gate("phase_g_linked_from_data_sources_doc", "04_analytical_data_layer.md" in DOC02.read_text(encoding="utf-8"))


def finish(start, fields, published):
    failed = [k for k, v in gates.items() if not v]
    summary = {"phase": "G", **fields, "gates": gates, "failed_gates": failed, "all_gates_pass": not failed,
               "elapsed_seconds": round(time.time() - start, 1)}
    VOUT.mkdir(parents=True, exist_ok=True)
    with open(VOUT / "phase_g_summary.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2, default=str, ensure_ascii=False)
    banner("PHASE G COMPLETE" if not failed else "PHASE G FAILED")
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass; views published: {published}")
    for k in failed:
        print(f"  FAILED: {k}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
