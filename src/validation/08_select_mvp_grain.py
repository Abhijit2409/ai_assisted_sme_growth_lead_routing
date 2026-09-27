"""
Phase F — choose and validate the MVP analytical scope, reporting spine and
layer-specific grains for the Canadian SME Acquisition Strategy.

Principle: there is no single grain at which all three sources join safely. The MVP
uses one common CMA reporting spine, tiered by evidence, with separate source-specific
fact views that keep statistical unit, period and source explicit. Nothing is
populated here: no analytical datasets, scores or market rankings are produced.

Design:
- Verifies Phases A-E (summaries, raw manifest, cache lineage, the eight crosswalks).
- Recomputes the evidence each decision rests on (tier membership, 0722 size-band
  availability and additivity, latest eligible months, pandemic quality flags,
  1097 sparsity) from lineage-checked caches and validated outputs.
- Assesses every candidate grain with published criteria and weights, an unweighted
  view and a weight-sensitivity test (a design-decision aid, not an opportunity score).
- Writes decision tables to data/validation/mvp_grain/ and checks every numeric claim
  in docs/03_mvp_analytical_scope.md. Any failed gate exits with status 1.

Run (Anaconda interpreter; works from any working directory):
    C:/Users/abhij/anaconda3/python.exe src/validation/08_select_mvp_grain.py
"""

from pathlib import Path
import itertools
import json
import sys
import time

import duckdb
import pandas as pd

sys.dont_write_bytecode = True          # keep src/validation free of __pycache__ when importing the helper
from cache_lineage import CACHE_SCHEMAS, check_lineage, expected_columns, sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VAL = ROOT / "data" / "validation"
XW = ROOT / "data" / "reference" / "crosswalks"
OUT = VAL / "mvp_grain"
MANIFEST = ROOT / "data" / "source_manifest.csv"
DOC03 = ROOT / "docs" / "03_mvp_analytical_scope.md"
DOC02 = ROOT / "docs" / "02_data_sources_and_validation.md"
RAW_0722 = ROOT / "data" / "raw" / "statcan_33100722" / "33100722-eng" / "33100722.csv"
RAW_1097 = ROOT / "data" / "raw" / "statcan_33101097" / "33101097-eng" / "33101097.csv"
CACHE_0722 = ROOT / "data" / "cache" / "statcan_33100722.parquet"
CACHE_1097 = ROOT / "data" / "cache" / "statcan_33101097.parquet"
SUMMARIES = {"A": VAL / "33100722" / "phase_a_summary.json", "B": VAL / "33101097" / "phase_b_summary.json",
             "C": VAL / "ised_2023" / "phase_c_summary.json", "D": VAL / "source_comparison" / "phase_d_summary.json",
             "E": VAL / "crosswalks" / "phase_e_summary.json"}
XW_FILES = ["cma_sgc2016_to_sgc2021.csv", "ised_cma_to_sgc2021.csv", "statcan_0722_industry_to_naics2022.csv",
            "ised_industry_to_naics2022.csv", "statcan_1097_size_to_sme_bands.csv", "ised_scope_approximation_rules.csv",
            "non_additive_category_rules.csv", "ised_region_province_crosswalk.csv"]

SCOPE_CLASSES = ["SME-specific measure", "small-business measure", "all-employer-business context",
                 "SME-location measure", "filtered SME survey measure", "diagnostic only"]

gates = {}


def gate(name, passed, detail=""):
    gates[name] = bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def banner(t):
    print("\n" + "=" * 90 + "\n" + t + "\n" + "=" * 90)


# ---------------------------------------------------
# 1. Upstream integrity
# ---------------------------------------------------

def upstream():
    banner("1. UPSTREAM INTEGRITY")
    for k, p in SUMMARIES.items():
        s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        g = s.get("gates", {})
        gate(f"upstream_phase_{k}_passes", bool(g) and all(g.values()) and s.get("all_gates_pass") is True, f"{sum(g.values())} of {len(g)}")
    manifest = pd.read_csv(MANIFEST, dtype=str).fillna("")
    shas = {r.relative_path: sha256_file(ROOT / r.relative_path) for r in manifest.itertuples()}
    gate("raw_files_match_manifest_before", all(shas[r.relative_path] == r.sha256 for r in manifest.itertuples()), f"{len(manifest)} files")
    con = duckdb.connect()
    for key, raw, cache in (("statcan_33100722", RAW_0722, CACHE_0722), ("statcan_33101097", RAW_1097, CACHE_1097)):
        ok, why, _ = check_lineage(con, root=ROOT, raw_path=raw, cache_path=cache, manifest_path=MANIFEST,
                                   schema_version=CACHE_SCHEMAS[key]["version"], expected_columns=expected_columns(key, raw),
                                   source_sha256=shas[raw.relative_to(ROOT).as_posix()])
        gate(f"cache_lineage_current_{key}", ok, why)
    e = json.loads(SUMMARIES["E"].read_text(encoding="utf-8"))
    gate("all_eight_crosswalks_exist_and_pass", all((XW / f).exists() for f in XW_FILES)
         and e["facts"]["checks_passed"] == e["facts"]["checks"], f"{e['facts']['checks_passed']} of {e['facts']['checks']} crosswalk checks")
    return manifest, shas, con


# ---------------------------------------------------
# 2. Evidence
# ---------------------------------------------------

def evidence(con):
    banner("2. EVIDENCE")
    e1 = pd.read_csv(XW / "cma_sgc2016_to_sgc2021.csv", dtype=str).fillna("")
    e2 = pd.read_csv(XW / "ised_cma_to_sgc2021.csv", dtype=str).fillna("")
    shared = set(e1[e1.mapping_status == "exact code with boundary caveat"].target_code)
    only = set(e1[e1.mapping_status == "target-only"].target_code)
    ised = set(e2.target_code)
    con.execute(f"CREATE VIEW a AS SELECT * FROM read_parquet('{CACHE_0722.as_posix()}')")
    con.execute(f"CREATE VIEW b AS SELECT * FROM read_parquet('{CACHE_1097.as_posix()}')")
    cma = "DGUID LIKE '2016S0503%'"

    latest = con.execute(f"""SELECT measure_id, ANY_VALUE("Business dynamics measure") AS measure,
        MIN(REF_DATE) FILTER (WHERE value_num IS NOT NULL) AS first_cma, MAX(REF_DATE) FILTER (WHERE value_num IS NOT NULL) AS latest_cma
        FROM a WHERE {cma} AND industry_id = 1 AND size_id = 1 GROUP BY 1 ORDER BY 1""").df()
    latest_ca = dict(con.execute("""SELECT measure_id, MAX(REF_DATE) FILTER (WHERE value_num IS NOT NULL)
        FROM a WHERE DGUID LIKE '%A000011124' AND industry_id = 1 AND size_id = 1 GROUP BY 1""").fetchall())
    latest["latest_canada"] = latest.measure_id.map(latest_ca)

    band_avail = con.execute(f"""SELECT measure_id, size_id, COUNT(DISTINCT DGUID) AS cmas
        FROM a WHERE {cma} AND industry_id = 1 AND REF_DATE = '2025-12' AND value_num IS NOT NULL GROUP BY 1, 2""").df()

    def cmas(m, s):
        r = band_avail[(band_avail.measure_id == m) & (band_avail.size_id == s)]
        return int(r.cmas.iloc[0]) if len(r) else 0
    add = con.execute(f"""WITH x AS (
        SELECT DGUID, REF_DATE, MAX(value_num) FILTER (WHERE size_id = 1) AS tot,
               SUM(value_num) FILTER (WHERE size_id BETWEEN 2 AND 6) AS bands, COUNT(value_num) FILTER (WHERE size_id BETWEEN 2 AND 6) AS nb,
               SUM(value_num) FILTER (WHERE size_id IN (2, 3)) AS small, COUNT(value_num) FILTER (WHERE size_id IN (2, 3)) AS ns,
               MAX(value_num) FILTER (WHERE size_id = 6) AS big
        FROM a WHERE {cma} AND industry_id = 1 AND measure_id = 1 GROUP BY 1, 2)
        SELECT COUNT(DISTINCT DGUID) FILTER (WHERE nb = 5) AS cmas_all_bands,
               MAX(ABS(100 * (bands - tot) / tot)) FILTER (WHERE nb = 5) AS max_abs_pct_diff,
               MEDIAN(100 * small / tot) FILTER (WHERE ns = 2) AS median_small_share,
               MEDIAN(100 * big / tot) FILTER (WHERE nb = 5) AS median_500_share FROM x""").df().iloc[0]
    eflags = con.execute("""SELECT REF_DATE, COUNT(*) FILTER (WHERE STATUS = 'E') AS e FROM a
        WHERE ref_year BETWEEN 2019 AND 2022 GROUP BY 1 ORDER BY 1""").df()
    peak = eflags[eflags.e >= 800]

    geo_b = pd.read_csv(VAL / "33101097" / "geography_profile.csv")
    naics_b = pd.read_csv(VAL / "33101097" / "naics_profile.csv", dtype={"naics_code": str})
    con.register("cm", geo_b[geo_b.geo_level == "CMA"][["geo_id"]])
    con.register("sec", naics_b[(naics_b.naics_level == "sector") & (naics_b.naics_code != "91")][["naics_id", "naics_code"]])
    band_sparsity = con.execute("""
        WITH r AS (SELECT geo_id, naics_id, CASE WHEN size_id = 2 THEN '1-4' WHEN size_id IN (3, 4) THEN '5-19'
                   WHEN size_id IN (5, 6) THEN '20-99' WHEN size_id IN (7, 8) THEN '100-499' END AS band, SUM(value_num) AS v
                   FROM b WHERE geo_id IN (SELECT geo_id FROM cm) AND naics_id IN (SELECT naics_id FROM sec) AND size_id BETWEEN 2 AND 8 GROUP BY ALL),
             grid AS (SELECT geo_id, naics_id, band FROM cm, sec, (VALUES ('1-4'), ('5-19'), ('20-99'), ('100-499')) t(band))
        SELECT band, COUNT(*) AS cells, ROUND(100.0 * COUNT(v) FILTER (WHERE v >= 10) / COUNT(*), 1) AS pct_cells_ge_10
        FROM grid LEFT JOIN r USING (geo_id, naics_id, band) GROUP BY 1""").df().set_index("band")
    sector_sme = con.execute("""
        WITH r AS (SELECT geo_id, naics_id, SUM(value_num) AS v FROM b WHERE geo_id IN (SELECT geo_id FROM cm)
                   AND naics_id IN (SELECT naics_id FROM sec) AND size_id BETWEEN 2 AND 8 GROUP BY ALL),
             grid AS (SELECT geo_id, naics_id FROM cm, sec)
        SELECT COUNT(*) AS cells, ROUND(100.0 * COUNT(v) FILTER (WHERE v >= 10) / COUNT(*), 1) AS pct_ge_10
        FROM grid LEFT JOIN r USING (geo_id, naics_id)""").df().iloc[0]
    size_cma = con.execute("""
        SELECT COUNT(*) AS cells, ROUND(100.0 * COUNT(*) FILTER (WHERE value_num >= 10) / COUNT(*), 1) AS pct_ge_10
        FROM b WHERE geo_id IN (SELECT geo_id FROM cm) AND naics_id = 1 AND size_id BETWEEN 2 AND 8""").df().iloc[0]
    c = json.loads(SUMMARIES["C"].read_text(encoding="utf-8"))
    rel = c["core_reliability_pct"]
    depth = pd.read_csv(VAL / "33100722" / "cma_publication_depth.csv")

    ev = {
        "tier1": len(shared & ised), "tier2": len(shared - ised), "tier3": len(only), "ised_outside_shared": len(ised - shared),
        "shared": len(shared), "all_1097": len(shared | only),
        "active_small_bands_cmas": min(cmas(1, 2), cmas(1, 3)), "opening_small_bands_cmas": min(cmas(2, 2), cmas(2, 3)),
        "closing_small_bands_cmas": min(cmas(4, 2), cmas(4, 3)),
        "active_all_bands_cmas": int(add.cmas_all_bands), "opening_all_bands_cmas": min(cmas(2, s) for s in range(2, 7)),
        "band_sum_max_pct_diff": round(float(add.max_abs_pct_diff), 3), "small_share_median": round(float(add.median_small_share), 1),
        "share_500_median": round(float(add.median_500_share), 1),
        "cma_measure_latest": latest.set_index("measure").latest_cma.fillna("not published").to_dict(),
        "latest_cma_month": latest.latest_cma.dropna().max(), "first_cma_month": latest.first_cma.dropna().min(),
        "exits_latest_canada": latest.set_index("measure").latest_canada.get("Exits"),
        "eflag_peak_first": peak.REF_DATE.min(), "eflag_peak_last": peak.REF_DATE.max(),
        "eflag_peak_months": len(peak),
        "cma_sector_detail_cmas": int((depth.naics_sectors_with_values > 0).sum()),
        "b_band_pct_ge10": band_sparsity.pct_cells_ge_10.to_dict(), "b_band_cells": int(band_sparsity.cells.iloc[0]),
        "b_sector_sme_pct_ge10": float(sector_sme.pct_ge_10), "b_sector_sme_cells": int(sector_sme.cells),
        "b_size_cma_pct_ge10": float(size_cma.pct_ge_10),
        "ised_t01_cma_AtoD": rel["ISED_T01|geography_cma"]["A_to_D"], "ised_t03_cma_AtoD": rel["ISED_T03|geography_cma"]["A_to_D"],
        "ised_t04_cma_F": rel["ISED_T04|geography_cma"]["F"], "ised_t05_cma_F": rel["ISED_T05|geography_cma"]["F"],
        "ised_t05_size_F": rel["ISED_T05|employment_size"]["F"],
    }
    for k, v in ev.items():
        print(f"  {k}: {v}")
    return ev, e1, e2


# ---------------------------------------------------
# 3. Candidate assessment
# ---------------------------------------------------

CRITERIA = [("relevance", 15), ("coverage", 15), ("sme_specificity", 10), ("reliability", 10), ("comparability", 10),
            ("interpretability", 10), ("suppression_exposure", 5), ("misleading_risk", 10), ("prioritization_support", 5),
            ("segmentation_support", 5), ("implementation_simplicity", 2.5), ("recruiter_readability", 2.5)]


def candidates(ev):
    # Scores 1 (poor) .. 5 (strong); 'suppression_exposure' and 'misleading_risk' score 5 = low exposure / low risk.
    C = [
        ("A", "35-CMA shared spine", f"{ev['shared']} CMAs in both 0722 and 1097",
         dict(relevance=4, coverage=4, sme_specificity=3, reliability=4, comparability=4, interpretability=4, suppression_exposure=4,
              misleading_risk=3, prioritization_support=4, segmentation_support=3, implementation_simplicity=4, recruiter_readability=4),
         f"Momentum and scale for all {ev['shared']}; but the {ev['tier3']} scale-only CMAs drop out of view and ISED gaps are implicit",
         "Rejected as stated: silently removes the six 1097-only CMAs; absorbed into F, which keeps A's decision set"),
        ("B", "24-CMA all-source subset", f"{ev['tier1']} CMAs with ISED CMA context",
         dict(relevance=4, coverage=2, sme_specificity=3, reliability=4, comparability=3, interpretability=3, suppression_exposure=4,
              misleading_risk=3, prioritization_support=3, segmentation_support=3, implementation_simplicity=3, recruiter_readability=3),
         "Every market has all three layers, but ISED is one-way context and not joinable at cell level",
         f"Rejected: drops {ev['tier2']} CMAs with valid StatCan evidence only because survey context is missing"),
        ("C", "41-CMA flat scale spine", f"{ev['all_1097']} CMAs in 1097",
         dict(relevance=3, coverage=5, sme_specificity=3, reliability=3, comparability=2, interpretability=3, suppression_exposure=3,
              misleading_risk=2, prioritization_support=3, segmentation_support=4, implementation_simplicity=3, recruiter_readability=3),
         f"Complete composition, but {ev['tier3']} CMAs have no momentum and would be compared as if evidence were equal",
         "Rejected as a flat spine: mixes evidence levels; its coverage is kept in F through tiers"),
        ("D", "18-CMA SME-band momentum subset", f"{ev['active_all_bands_cmas']} CMAs with all five active-enterprise bands",
         dict(relevance=3, coverage=1, sme_specificity=3, reliability=3, comparability=3, interpretability=3, suppression_exposure=2,
              misleading_risk=3, prioritization_support=2, segmentation_support=2, implementation_simplicity=2, recruiter_readability=3),
         f"Openings/closings are published for all five bands in {ev['opening_all_bands_cmas']} CMAs, so SME-specific rates are impossible even here",
         "Rejected: no SME-specific opening or closing rate exists; coverage bias"),
        ("E", "35-CMA small-business bands", f"1-4 and 5-19 bands (active in {ev['active_small_bands_cmas']} CMAs)",
         dict(relevance=3, coverage=3, sme_specificity=3, reliability=3, comparability=3, interpretability=3, suppression_exposure=2,
              misleading_risk=3, prioritization_support=3, segmentation_support=2, implementation_simplicity=3, recruiter_readability=3),
         f"Active small-band counts cover {ev['active_small_bands_cmas']} CMAs but openings/closings only {ev['opening_small_bands_cmas']}; excludes 20-499",
         f"Rejected as primary: small-band flow rates cover only {ev['opening_small_bands_cmas']} CMAs; kept as an optional view"),
        ("F", "Tiered 41-CMA spine, 35-CMA primary decision set", f"41 CMAs visible; tiers {ev['tier1']}/{ev['tier2']}/{ev['tier3']}",
         dict(relevance=5, coverage=5, sme_specificity=3, reliability=4, comparability=4, interpretability=4, suppression_exposure=4,
              misleading_risk=5, prioritization_support=4, segmentation_support=4, implementation_simplicity=3, recruiter_readability=4),
         "Keeps every market visible, decides only where momentum and scale both exist, and labels evidence level",
         ""),
    ]
    rows = []
    for cid, name, cov, sc, strengths, rejection in C:
        w = sum(sc[k] * wt for k, wt in CRITERIA) / sum(wt for _, wt in CRITERIA)
        rows.append({"candidate": cid, "name": name, "coverage_basis": cov, **{f"score_{k}": sc[k] for k, _ in CRITERIA},
                     "weighted_score": round(w, 3), "unweighted_mean": round(sum(sc.values()) / len(sc), 3),
                     "assessment": strengths, "rejection_reason": rejection,
                     "label": "design-decision aid, not an opportunity score"})
    df = pd.DataFrame(rows)
    # Sensitivity: each weight +/-25% (one at a time) and equal weights.
    winners = set()
    for (k, wt), f in itertools.product(CRITERIA, (0.75, 1.25)):
        ww = {kk: (vv * f if kk == k else vv) for kk, vv in CRITERIA}
        s = {r.candidate: sum(getattr(r, f"score_{kk}") * ww[kk] for kk in ww) for r in df.itertuples()}
        winners.add(max(s, key=s.get))
    winners.add(df.loc[df.unweighted_mean.idxmax(), "candidate"])
    return df, winners


# ---------------------------------------------------
# 4. Decisions
# ---------------------------------------------------

def decisions(ev, e1, e2):
    tier = {}
    for r in e1.itertuples():
        code = r.target_code
        if r.mapping_status == "target-only":
            tier[code] = 3
        else:
            tier[code] = 1 if code in set(e2.target_code) else 2
    spine = e1.assign(cma_code_sgc2021=e1.target_code, tier=e1.target_code.map(tier))[
        ["cma_code_sgc2021", "target_label", "target_dguid", "source_code", "source_dguid", "mapping_status", "boundary_change_flag", "tier"]]
    spine = spine.rename(columns={"target_label": "cma_label_sgc2021", "target_dguid": "dguid_2021", "source_code": "cma_code_sgc2016",
                                  "source_dguid": "dguid_2016", "mapping_status": "vintage_status"})
    lab = dict(zip(e2.target_code, e2.source_label))
    spine["ised_cma_label"] = spine.cma_code_sgc2021.map(lab).fillna("")
    spine["has_momentum_0722"] = spine.tier.isin([1, 2])
    spine["has_scale_1097"] = True
    spine["has_ised_cma_context"] = spine.tier == 1
    spine["in_primary_decision_set"] = spine.tier.isin([1, 2])
    spine["evidence_label"] = spine.tier.map({1: "momentum + scale + ISED CMA context", 2: "momentum + scale; ISED regional/national context only",
                                              3: "scale only (no 0722 momentum); ISED regional context only"})
    spine = spine.sort_values(["tier", "cma_code_sgc2021"]).reset_index(drop=True)

    tiers = pd.DataFrame([
        (1, "Full evidence", ev["tier1"], "0722 momentum (monthly, all-employer), 1097 scale and composition, ISED CMA-row context",
         "Primary decision set", "Compare within and with Tier 2 on StatCan measures; ISED only as labelled context"),
        (2, "StatCan evidence, no ISED CMA context", ev["tier2"], "0722 momentum, 1097 scale and composition; ISED at region/national level (E8)",
         "Primary decision set", "Same StatCan comparisons as Tier 1; ISED gap shown, never imputed"),
        (3, "Scale-only", ev["tier3"], "1097 scale and composition only; new 2021 CMAs with no 0722 series",
         "Visible, not ranked with Tiers 1-2", "Report scale and composition; never rank on momentum; flag 'no momentum data'"),
    ], columns=["tier", "tier_name", "cmas", "evidence_available", "decision_role", "rule"])

    L = pd.DataFrame([
        ("momentum", "33-10-0722-01", "active employer enterprises and flows", "CMA (SGC 2016 code, bridged to 2021)",
         "Business sector total (T004); sector view optional for the 8 CMAs with detail", "All sizes (primary); 1-4 and 5-19 optional",
         f"Monthly, {ev['first_cma_month']} to {ev['latest_cma_month']}", "Trailing 12 and 36 months; pandemic window flagged",
         "Own active-enterprise denominator only"),
        ("scale_and_composition", "33-10-1097-01", "employer locations", "CMA (SGC 2021); CSD excluded from MVP",
         "NAICS 2022 sector (19 business sectors; 91 and Unclassified shown separately)", "1-4, 5-19, 20-99, 100-499 (exact rollup); 500+ separate",
         "December 2025 counts (snapshot)", "None (not a time series)", "Shares within 1097 only"),
        ("financing_context", "ISED SFGSME 2023", "weighted survey estimates of SMEs (1-499, >= $30k revenue)",
         "CMA row (Tier 1 only, via E2); region/province via E8; national", "10 ISED industry groups (one-way)",
         "4 ISED SME bands (one-way)", "Calendar 2023", "None (single cycle)", "Contextual attributes only; one-way segments"),
    ], columns=["layer", "source", "statistical_unit", "geographic_grain", "industry_grain", "size_grain", "period", "trend_window", "denominator_rule"])
    return spine, tiers, L


def metrics(ev):
    M = []

    def m(mid, name, layer, source, unit, period, grain, scope, formula, denom_source, eligibility, quality, prohibited, estimate_type="count"):
        M.append({"metric_id": mid, "metric": name, "layer": layer, "authoritative_source": source, "statistical_unit": unit,
                  "period": period, "grain": grain, "scope_class": scope, "formula": formula, "denominator_source": denom_source,
                  "eligibility": eligibility, "quality_rule": quality, "prohibited_uses": prohibited, "estimate_type": estimate_type,
                  "count_conversion_allowed": False if source == "ISED" else "n/a",
                  "includes_500_plus": scope == "all-employer-business context" or "500+" in name,
                  "size_scope": ("all sizes (incl. 500+)" if scope == "all-employer-business context"
                                 else "1-4 and 5-19 bands" if scope == "small-business measure"
                                 else "1-499" if scope in ("SME-location measure", "filtered SME survey measure") else "1-499 (filtered)")})
    P12 = f"monthly; trailing 12 months ending {ev['latest_cma_month']}"
    x0722 = "'x'/'..' unavailable, never zero; separately seasonally adjusted"
    m("M01", "Active employer enterprises (all sizes)", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "published value", "0722", "core", x0722, "Never divided by 1097 locations; never summed across CMAs or industries")
    m("M02", "Openings (all sizes)", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "published value", "0722", "core", x0722, "Never divided by 1097 locations")
    m("M03", "Closures (all sizes)", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "published value", "0722", "core", x0722, "Never divided by 1097 locations")
    m("M04", "Net formation (openings - closures)", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "openings(t) - closures(t)", "0722", "core", x0722, "Not exits-based (exits lag 6 months and are not published for CMAs)")
    m("M05", "Opening rate", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "openings(t) / active(t-1)", "0722", "core", x0722, "Denominator must be 0722 active")
    m("M06", "Closure rate", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "closures(t) / active(t-1)", "0722", "core", x0722, "Denominator must be 0722 active")
    m("M07", "Net formation rate", "momentum", "0722", "enterprise", P12, "CMA x month (T004)", "all-employer-business context",
      "(openings(t) - closures(t)) / active(t-1)", "0722", "core", x0722, "Denominator must be 0722 active")
    m("M08", "36-month trend of opening and net formation rates", "momentum", "0722", "enterprise", "monthly; trailing 36 months",
      "CMA x month (T004)", "all-employer-business context", "trailing average / slope of M05, M07", "0722", "supporting", x0722,
      "Pandemic window flagged, not silently dropped")
    m("M09", "Active enterprises, 1-4 and 5-19 employees (separately)", "momentum", "0722", "enterprise", P12, "CMA x month x band",
      "small-business measure", "published values, bands kept separate", "0722", "optional", x0722,
      f"Not an SME total; 20-499 bands only in {ev['active_all_bands_cmas']} CMAs")
    m("M10", "Small-business opening rate (1-4, 5-19)", "momentum", "0722", "enterprise", P12, "CMA x month x band", "small-business measure",
      "openings(band, t) / active(band, t-1)", "0722", "optional (limited coverage)", x0722,
      f"Published for only {ev['opening_small_bands_cmas']} CMAs; never extrapolated")
    m("M11", "Sector momentum", "momentum", "0722", "enterprise", "monthly from 2018-01", "CMA x 0722 sector member",
      "all-employer-business context", "M01-M07 by sector", "0722", "optional (8 CMAs)", x0722,
      "Only for the CMAs with sector detail; tourism and [11] excluded from cross-source views")
    m("M12", "Exits and temporary closures", "momentum", "0722", "enterprise", f"monthly to {ev['exits_latest_canada']}", "Canada/province",
      "all-employer-business context", "published value", "0722", "excluded from CMA MVP", "6-month lag (Note 10); " + x0722, "Not published for CMAs")
    m("S01", "Employer locations, all sizes", "scale_and_composition", "1097", "location", "December 2025", "CMA",
      "all-employer-business context", "published total", "1097", "context", "none suppressed; omitted = 0",
      "Not businesses or potential clients; not a 0722 denominator")
    m("S02", "SME employer locations (1-499)", "scale_and_composition", "1097", "location", "December 2025", "CMA",
      "SME-location measure", "sum of bands 1-4 ... 200-499 (E5 exact rollup)", "1097", "core", "exact rollup; omitted = 0",
      "Not a count of SMEs; not a 0722 denominator")
    m("S03", "Sector share of classified SME locations", "scale_and_composition", "1097", "location", "December 2025", "CMA x NAICS sector",
      "SME-location measure", "sector 1-499 / classified 1-499 locations in the 19 business sectors", "1097", "core",
      "Unclassified and 91 excluded from the denominator and shown separately", "Never allocate Unclassified; never mix with 0722 or ISED")
    m("S04", "Size-band share of SME locations", "scale_and_composition", "1097", "location", "December 2025", "CMA x broad band",
      "SME-location measure", "band / SME locations (1-499)", "1097", "core", "exact rollup (E5)", "Location-based; not enterprise size")
    m("S05", "Unclassified share of SME locations", "scale_and_composition", "1097", "location", "December 2025", "CMA",
      "SME-location measure", "Unclassified 1-499 / SME locations 1-499", "1097", "quality context", "shown with S03", "Never redistributed")
    m("S06", "CMA x sector x broad band locations", "scale_and_composition", "1097", "location", "December 2025", "CMA x sector x band",
      "SME-location measure", "rolled bands by sector", "1097", "optional",
      f"sparse: {ev['b_band_pct_ge10']['100-499']}% of 100-499 cells have 10+ locations", "Not a core ranking input")
    m("S07", "Approximate ISED-scope employer locations", "scale_and_composition", "1097", "location", "December 2025", "CMA",
      "diagnostic only", "E6 observable filters", "1097", "diagnostic", "E6 rules", "Never called ISED-eligible SMEs; never compared with ISED weighted counts")
    iq = "A-D shown; E shown with caution flag; F excluded; '..' unavailable (never zero); rank only if |diff| > 2 x combined SE"
    m("I01", "External-financing request rate (Table 1)", "financing_context", "ISED", "survey estimate (% of SMEs)", "2023",
      "one-way: CMA (Tier 1), region, size band, industry group, national", "filtered SME survey measure", "published %", "ISED",
      "contextual", iq, "Never converted to counts; never multiplied across dimensions", "percentage")
    m("I02", "Debt request rate (Table 3)", "financing_context", "ISED", "survey estimate (% of SMEs)", "2023", "one-way segments",
      "filtered SME survey measure", "published %", "ISED", "contextual", iq, "Never converted to counts", "percentage")
    m("I03", "Debt approval rate (Table 3)", "financing_context", "ISED", "survey estimate (% of requesters)", "2023", "one-way segments",
      "filtered SME survey measure", "published %", "ISED", "contextual", iq, "Denominator is requesters, not all SMEs", "percentage")
    m("I04", "Instrument request and approval rates (Table 4)", "financing_context", "ISED", "survey estimate (%)", "2023", "one-way segments",
      "filtered SME survey measure", "published %", "ISED", "contextual", iq + f"; {ev['ised_t04_cma_F']}% of CMA estimates are F",
      "Instruments overlap; never summed", "percentage")
    m("I05", "Requested and authorized amounts (Table 5)", "financing_context", "ISED", "survey estimate (dollars)", "2023",
      "national and size band only", "filtered SME survey measure", "published $", "ISED", "contextual (national/size only)",
      iq + f"; CMA level excluded ({ev['ised_t05_cma_F']}% F)", "No CMA or industry deal size", "monetary")
    m("I06", "Weighted SME count by segment (Tables 49/50)", "financing_context", "ISED", "survey estimate (enterprises)", "2023",
      "one-way segments; Table 50 two-way crosses", "filtered SME survey measure", "published weighted count", "ISED", "contextual",
      iq, "Never a register count; never compared with 1097 locations or 0722 enterprises", "weighted count")
    return pd.DataFrame(M)


def industry_size_time(ev):
    ind = pd.DataFrame([
        ("1097 NAICS sectors (19 business sectors)", "scale_and_composition", "primary CMA industry-composition grain",
         f"CMA x sector (1-499): {ev['b_sector_sme_pct_ge10']}% of cells have 10+ locations", "include"),
        ("1097 public administration (91)", "scale_and_composition", "shown separately; outside SME banking-acquisition scope and ISED scope", "", "exclude from composition"),
        ("1097 Unclassified", "scale_and_composition", "carried separately (E7); excluded from sector-share denominator", "", "separate display"),
        ("1097 4-digit industry groups", "scale_and_composition", "not in MVP: sparse and hard to read (Phase B: CMA median 11 locations per group)", "", "exclude"),
        ("ISED 10 broad groups", "financing_context", "separate financing-context grain (one-way)", "E4", "include (context)"),
        ("ISED 8 special aggregates", "financing_context", "overlap groups; not in MVP views", "E7", "exclude"),
        ("0722 sector momentum", "momentum", f"optional view for the {ev['cma_sector_detail_cmas']} CMAs with detail", "E3", "optional"),
        ("0722 tourism aggregates and components", "momentum", "overlap sectors; excluded", "E7", "exclude"),
        ("0722 [11] Forestry, fishing and hunting", "momentum", "scope unresolved; excluded from cross-source sector views", "E3", "exclude from cross-source"),
        ("0722 partial 61 and 62", "momentum", "partial sectors; shown with their own labels, never compared with 1097 61/62", "E3", "own-label only"),
    ], columns=["category", "layer", "decision", "evidence", "treatment"])
    b = ev["b_band_pct_ge10"]
    size = pd.DataFrame([
        ("1-4", "SME", f"{ev['active_small_bands_cmas']} CMAs (active); {ev['opening_small_bands_cmas']} (flows)", "all 41 CMAs (exact)", "national/one-way", f"{b['1-4']}%"),
        ("5-19", "SME", f"{ev['active_small_bands_cmas']} CMAs (active); {ev['opening_small_bands_cmas']} (flows)", "all 41 CMAs (from 5-9 + 10-19)", "national/one-way", f"{b['5-19']}%"),
        ("20-99", "SME", f"{ev['active_all_bands_cmas']} CMAs (active only)", "all 41 CMAs (from 20-49 + 50-99)", "national/one-way", f"{b['20-99']}%"),
        ("100-499", "SME", f"{ev['active_all_bands_cmas']} CMAs (active only)", "all 41 CMAs (from 100-199 + 200-499)", "national/one-way", f"{b['100-499']}%"),
        ("500+", "outside SME scope (shown separately)", f"{ev['active_all_bands_cmas']} CMAs (active only)", "all 41 CMAs", "not in ISED", ""),
    ], columns=["band", "scope", "support_0722", "support_1097_locations", "support_ised", "cma_x_sector_cells_ge_10_1097"])
    time_ = pd.DataFrame([
        ("0722 active, openings, continuing, closings", "monthly", f"{ev['first_cma_month']} to {ev['latest_cma_month']}", ev["latest_cma_month"],
         f"primary: trailing 12 months ending {ev['latest_cma_month']}; supporting: trailing 36 months",
         f"pandemic window {ev['eflag_peak_first']}-2021-12 flagged (E-flag peak {ev['eflag_peak_first']} to {ev['eflag_peak_last']}); excluded from baselines, not deleted",
         "'0722 monthly, seasonally adjusted, <window>'"),
        ("0722 exits, temporary closures", "monthly", f"to {ev['exits_latest_canada']}", ev["exits_latest_canada"], "not in CMA MVP",
         "six-month lag (Note 10)", "'0722 national context, to <month>'"),
        ("0722 sector detail (8 CMAs)", "monthly", "2018-01 onward", ev["latest_cma_month"], "trailing 12 months", "starts 2018-01", "'0722 sector view, 8 CMAs'"),
        ("1097 locations", "snapshot", "December 2025 counts", "December 2025", "none", "not a time series (Note 3)", "'1097 locations, December 2025'"),
        ("ISED financing context", "single cycle", "calendar 2023", "2023", "none", "about two years older than 1097", "'ISED survey 2023'"),
    ], columns=["layer_measures", "frequency", "available_period", "latest_eligible", "window_rule", "caveat", "report_label"])
    return ind, size, time_


def model_spec():
    V = [
        ("dim_cma", "dimension", "Reporting spine: every CMA with tier and vintage flags", "1097 + E1 + E2", "CMA",
         "one row per SGC 2021 CMA", "cma_code_sgc2021", "", "", "tier, has_momentum_0722, has_ised_cma_context, boundary_change_flag",
         "Never join on labels", "41 CMAs"),
        ("dim_industry", "dimension", "Industry members by system", "E3, E4, 1097", "classification", "one row per system x code",
         "industry_system, industry_code", "", "", "additive_flag, overlap_flag", "Never sum overlapping members", "1097 sectors, 0722 members, ISED groups"),
        ("dim_size_band", "dimension", "Broad bands 1-4, 5-19, 20-99, 100-499, 500+", "E5", "classification", "one row per band",
         "size_band_key", "", "", "in_sme_scope", "", "5 bands + total"),
        ("fact_cma_momentum_monthly", "fact", "Enterprise dynamics per CMA and month", "0722 only", "enterprise",
         "CMA x month x size scope x industry member x measure", "cma_code_sgc2016, ref_month, size_band_key, industry_code_0722, measure",
         "cma_code_sgc2016 -> bridge_cma_vintage", "ref_month", "status, value_available, pandemic_window_flag, seasonally_adjusted",
         "No 1097 or ISED values; no 1097 denominators; no sums across CMAs or industries", "35 CMAs"),
        ("fact_cma_market_scale_snapshot", "fact", "Employer locations per CMA (all sizes and 1-499)", "1097 only", "location",
         "CMA x size scope", "cma_code_sgc2021, size_scope, snapshot_period", "cma_code_sgc2021 -> dim_cma", "snapshot_period",
         "omitted_zero_flag", "Not businesses/clients; not a 0722 denominator", "41 CMAs"),
        ("fact_cma_sector_composition_snapshot", "fact", "SME locations by NAICS sector", "1097 only", "location",
         "CMA x NAICS sector (1-499)", "cma_code_sgc2021, naics_sector, snapshot_period", "cma_code_sgc2021 -> dim_cma; naics_sector -> dim_industry",
         "snapshot_period", "unclassified_separate, excluded_91_flag", "No Unclassified allocation; no ISED/0722 values", "41 CMAs x 19 sectors"),
        ("fact_cma_size_composition_snapshot", "fact", "SME locations by broad size band", "1097 only", "location",
         "CMA x broad band", "cma_code_sgc2021, size_band_key, snapshot_period", "cma_code_sgc2021 -> dim_cma; size_band_key -> dim_size_band",
         "snapshot_period", "location_basis_flag", "Not enterprise size", "41 CMAs x 5 bands"),
        ("fact_ised_financing_context", "fact", "ISED survey estimates by one-way segment", "ISED only", "survey estimate",
         "ISED table x measure x one-way segment", "ised_table, measure_id, segment_dimension, segment_key", "segment_key -> dim_cma (via E2) / region (E8) / dim_size_band / ISED group",
         "survey_year", "quality_code, se_or_cv, dotdot_flag, caution_flag", "No counts from %; no cross-dimensional multiplication; F excluded", "Tier 1 CMAs + regions + national"),
        ("bridge_cma_vintage", "bridge", "SGC 2016 -> SGC 2021 CMA identity with caveats", "E1", "classification", "one row per CMA pair",
         "cma_code_sgc2016, cma_code_sgc2021", "", "", "boundary_change_flag", "Never treated as exact boundaries", "35 + 6 target-only"),
        ("bridge_industry", "bridge", "0722 members and ISED groups -> NAICS 2022", "E3, E4", "classification", "one row per source-target pair",
         "source_system, source_code, target_code", "", "", "mapping_status, additive_flag", "No allocation of values across one-to-many rows", "40 + 23 rows"),
        ("bridge_size_band", "bridge", "1097 bands -> broad bands", "E5", "classification", "one row per 1097 band",
         "source_band, target_band", "", "", "unit_basis", "Location-based only", "8 bands + total"),
    ]
    return pd.DataFrame(V, columns=["view", "view_type", "business_purpose", "source", "statistical_unit", "grain", "primary_key",
                                    "foreign_keys", "period_fields", "quality_and_scope_flags", "prohibited_calculations", "expected_coverage"])


def caveats(ev):
    X = [
        ("single combined fact table", "prohibited", "units, periods and estimate types differ (Phase D)"),
        ("0722 flows / 1097 locations", "prohibited", "unit mismatch"),
        ("ISED % converted to counts or multiplied across dimensions", "prohibited", "survey prevalence, one-way segments"),
        ("ISED F estimates", "excluded", "source: do not use"),
        ("ISED E estimates", "caution flag + sensitivity", "20-30% CV / 10-15 SE"),
        ("'..' and 'x'", "unavailable, never zero", "Phases A and C"),
        ("name-based joins", "prohibited", "codes only (E1, E2, E8)"),
        ("Tier 3 CMAs ranked with Tiers 1-2", "prohibited", f"{ev['tier3']} CMAs have no momentum"),
        ("Missing ISED CMA context (Tiers 2-3)", "shown as missing; regional context via E8 labelled as regional", "never imputed"),
        ("all-size 0722 series labelled SME", "prohibited", f"500+ is about {ev['share_500_median']}% of active enterprises (median, 18 CMAs) but still outside scope"),
        ("SME-specific momentum rates", "not available", f"all-band openings in {ev['opening_all_bands_cmas']} CMAs"),
        ("2016 vs 2021 CMA boundaries", "caveat on every cross-layer CMA view", "E1 boundary flags"),
        ("ISED geography vintage", "not stated; caveat on Tier 1 context", "E2"),
        ("Approximate ISED-scope locations", "diagnostic only", "never ISED-eligible SMEs"),
        ("CSD detail, 4-digit industries, CMA x sector x 100-499", "excluded from MVP", "sparsity and readability"),
        ("Public data to leads", "prohibited", "aggregates never identify businesses"),
        ("Synthetic CRM layer", "kept separate", "later phase; never mixed with public aggregates"),
    ]
    return pd.DataFrame(X, columns=["item", "treatment", "reason"])


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():
    start = time.time()
    manifest, shas, con = upstream()
    if not all(gates.values()):
        return finish(start, {})
    OUT.mkdir(parents=True, exist_ok=True)
    ev, e1, e2 = evidence(con)

    banner("3. CANDIDATES AND DECISIONS")
    cand, winners = candidates(ev)
    spine, tiers, layers = decisions(ev, e1, e2)
    mets = metrics(ev)
    ind, size, time_ = industry_size_time(ev)
    model = model_spec()
    cav = caveats(ev)
    selected = cand.loc[cand.weighted_score.idxmax(), "candidate"]
    print(cand[["candidate", "name", "weighted_score", "unweighted_mean"]].to_string(index=False))
    print(f"  selected: {selected}; winners under sensitivity: {sorted(winners)}")
    for df, name in ((cand, "candidate_grain_assessment.csv"), (spine, "selected_reporting_spine.csv"), (tiers, "geographic_coverage_tiers.csv"),
                     (layers, "layer_grain_decisions.csv"), (mets, "metric_eligibility.csv"), (ind, "industry_scope_decision.csv"),
                     (size, "size_scope_decision.csv"), (time_, "time_scope_decision.csv"), (model, "analytical_model_spec.csv"),
                     (cav, "exclusions_and_caveats.csv")):
        df.to_csv(OUT / name, index=False, encoding="utf-8")

    banner("4. GATES")
    gate("every_required_candidate_assessed", set("ABCDE") <= set(cand.candidate))
    gate("exactly_one_primary_spine_selected", selected == "F" and cand.weighted_score.max() > cand.weighted_score.nlargest(2).iloc[1])
    gate("decision_stable_under_weight_sensitivity_and_unweighted", winners == {selected}, f"winners: {sorted(winners)}")
    gate("alternatives_have_rejection_reasons", (cand[cand.candidate != selected].rejection_reason.str.len() > 0).all())
    gate("tier_structure_supported_by_crosswalks", (ev["tier1"], ev["tier2"], ev["tier3"], ev["ised_outside_shared"]) == (24, 11, 6, 0),
         f"{ev['tier1']}/{ev['tier2']}/{ev['tier3']}")
    gate("six_scale_only_cmas_visible", (spine.tier == 3).sum() == 6 and len(spine) == 41)
    gate("missing_ised_coverage_visible", (~spine.has_ised_cma_context).sum() == ev["tier2"] + ev["tier3"])
    gate("each_layer_has_explicit_grain", layers[["geographic_grain", "industry_grain", "size_grain", "period"]].notna().all().all() and len(layers) == 3)
    gate("each_metric_has_one_authoritative_source", mets.authoritative_source.isin(["0722", "1097", "ISED"]).all() and mets.metric_id.is_unique)
    gate("each_metric_has_unit_and_period", (mets.statistical_unit.str.len() > 0).all() and (mets.period.str.len() > 0).all())
    sme = mets[mets.scope_class.isin(["SME-specific measure", "SME-location measure", "filtered SME survey measure"])]
    gate("500_plus_excluded_from_sme_measures", not sme.includes_500_plus.any())
    m0722 = mets[mets.authoritative_source == "0722"]
    gate("all_size_0722_labelled_all_employer_context",
         (m0722[m0722.grain.str.contains("T004|sector member|Canada")].scope_class == "all-employer-business context").all()
         and not (m0722.scope_class.isin(["SME-specific measure", "SME-location measure"])).any())
    gate("1097_counts_labelled_locations", (mets[mets.authoritative_source == "1097"].statistical_unit == "location").all())
    gate("ised_measures_labelled_survey_estimates", mets[mets.authoritative_source == "ISED"].statistical_unit.str.startswith("survey estimate").all())
    iq = mets[mets.authoritative_source == "ISED"].quality_rule
    gate("ised_F_excluded_and_E_flagged", iq.str.contains("F excluded").all() and iq.str.contains("E shown with caution").all())
    gate("dotdot_never_zero", mets[mets.authoritative_source != "1097"].quality_rule.str.contains("never zero").all())
    gate("no_1097_value_as_0722_denominator", (mets[mets.authoritative_source == "0722"].denominator_source == "0722").all())
    gate("no_ised_percentage_converted_to_count", (mets[mets.authoritative_source == "ISED"].count_conversion_allowed == False).all())  # noqa: E712
    gate("no_cross_dimensional_ised_multiplication", mets[mets.authoritative_source == "ISED"].grain.str.contains("one-way|national").all()
         and "one-way" in model.set_index("view").loc["fact_ised_financing_context", "grain"])
    keys = " ".join(model.primary_key.tolist() + model.foreign_keys.tolist()).lower()
    gate("no_name_based_join_specified", "label" not in keys and "name" not in keys)
    facts_src = model[model.view_type == "fact"].source
    gate("no_single_combined_fact_table", facts_src.str.endswith(" only").all())
    gate("every_view_has_primary_key", (model.primary_key.str.len() > 0).all())

    facts = {**{k: v for k, v in ev.items() if not isinstance(v, dict)}, "selected_candidate": selected,
             "selected_score": float(cand.weighted_score.max()), "runner_up_score": float(cand.weighted_score.nlargest(2).iloc[1]),
             "candidates": len(cand), "metrics": len(mets), "core_metrics": int((mets.eligibility == "core").sum()),
             "views": len(model), "fact_views": int((model.view_type == "fact").sum()),
             "b_band_100_499_pct_ge10": ev["b_band_pct_ge10"]["100-499"]}

    banner("5. DOCUMENTATION")
    F = {k: (f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)) for k, v in facts.items()}
    claims = {
        "tiers": f"**{F['tier1']}** Tier 1, **{F['tier2']}** Tier 2 and **{F['tier3']}** Tier 3 CMAs",
        "flows": f"openings and closings for the 1–4 and 5–19 bands in only **{F['opening_small_bands_cmas']}** CMAs",
        "all_bands": f"all five bands in only **{F['active_all_bands_cmas']}** CMAs",
        "no_sme_rates": f"openings for all five bands in **{F['opening_all_bands_cmas']}** CMAs",
        "band_sum": f"within **{F['band_sum_max_pct_diff']}%**",
        "small_share": f"**{F['small_share_median']}%** of active enterprises",
        "share500": f"about **{F['share_500_median']}%**",
        "latest": f"**{F['latest_cma_month']}**",
        "exits": f"**{F['exits_latest_canada']}**",
        "sparse": f"**{F['b_band_100_499_pct_ge10']}%** of CMA × sector cells",
        "sector_sme": f"**{F['b_sector_sme_pct_ge10']}%** of CMA × sector cells",
        "t05": f"**{F['ised_t05_cma_F']}%** F",
        "score": f"**{F['selected_score']}**",
        "runner": f"**{F['runner_up_score']}**",
        "metrics": f"**{F['metrics']}** metrics, **{F['core_metrics']}** core",
        "views": f"**{F['views']}** views, **{F['fact_views']}** fact tables",
    }
    doc = DOC03.read_text(encoding="utf-8") if DOC03.exists() else ""
    missing = {k: v for k, v in claims.items() if v not in doc}
    gate("documentation_numbers_reproduced_by_outputs", bool(doc) and not missing,
         ("NOT YET DOCUMENTED" if not doc else f"{len(claims) - len(missing)} of {len(claims)} claims") + (f"; missing {missing}" if doc and missing else ""))
    gate("phase_f_linked_from_data_sources_doc", "03_mvp_analytical_scope.md" in DOC02.read_text(encoding="utf-8"))
    changed = [r.relative_path for r in manifest.itertuples() if sha256_file(ROOT / r.relative_path) != shas[r.relative_path]]
    gate("no_raw_file_changed", not changed, f"{len(manifest)} files")
    con.close()
    finish(start, {"facts": facts, "doc_claims": claims, "sensitivity_winners": sorted(winners)})


def finish(start, fields):
    failed = [k for k, v in gates.items() if not v]
    summary = {"phase": "F", **fields, "gates": gates, "failed_gates": failed, "all_gates_pass": not failed,
               "elapsed_seconds": round(time.time() - start, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "phase_f_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str, ensure_ascii=False)
    banner("PHASE F COMPLETE" if not failed else "PHASE F FAILED")
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass")
    for k in failed:
        print(f"  FAILED: {k}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
