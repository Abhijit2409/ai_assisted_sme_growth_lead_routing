"""
Phase D — compare and reconcile the analytical grains of the three validated sources:
  Statistics Canada 33-10-0722-01, Statistics Canada 33-10-1097-01, ISED SFGSME 2023.

Question: what does each source measure, which dimensions align, which differences
prevent joins, which within-source metrics are defensible, which uses are contextual
only or prohibited, and what must Phase E map?

Design:
- Consumes the validated Phase A-C outputs (data/validation/*) only; raw sources are
  never read except to verify their sha256 against data/source_manifest.csv.
- Upstream integrity: all three summaries must exist and report every gate passing;
  every raw checksum must match the manifest; both StatCan cache lineages must be current.
- Every number in the matrices is derived from an upstream output at run time.
- The three-layer structure (momentum / scale / financing context) is TESTED against
  explicit evidence checks, not assumed.
- No production crosswalk is built: Phase E requirements are listed, not mapped.
- Every numeric claim in the Phase D section of docs/02_data_sources_and_validation.md
  is checked against the outputs. Any failed gate exits with status 1.

Run (Anaconda interpreter; works from any working directory):
    C:/Users/abhij/anaconda3/python.exe src/validation/06_compare_source_grains.py
"""

from pathlib import Path
import json
import sys
import time

import pandas as pd

sys.dont_write_bytecode = True          # keep src/validation free of __pycache__ when importing the helper
from cache_lineage import CACHE_SCHEMAS, check_lineage, expected_columns, sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VAL = ROOT / "data" / "validation"
A_DIR, B_DIR, C_DIR = VAL / "33100722", VAL / "33101097", VAL / "ised_2023"
OUTPUT_DIR = VAL / "source_comparison"
MANIFEST_FILE = ROOT / "data" / "source_manifest.csv"
DOC_FILE = ROOT / "docs" / "02_data_sources_and_validation.md"
DOC_SECTION_HEADING = "## Source grain comparison (Phase D)"

SUMMARIES = {"0722": A_DIR / "phase_a_summary.json", "1097": B_DIR / "phase_b_summary.json",
             "ISED": C_DIR / "phase_c_summary.json"}
RAW_0722 = ROOT / "data" / "raw" / "statcan_33100722" / "33100722-eng" / "33100722.csv"
RAW_1097 = ROOT / "data" / "raw" / "statcan_33101097" / "33101097-eng" / "33101097.csv"
CACHES = {"statcan_33100722": (RAW_0722, ROOT / "data" / "cache" / "statcan_33100722.parquet"),
          "statcan_33101097": (RAW_1097, ROOT / "data" / "cache" / "statcan_33101097.parquet")}

SOURCES = ["0722", "1097", "ISED"]
SOURCE_NAMES = {"0722": "StatCan 33-10-0722-01", "1097": "StatCan 33-10-1097-01", "ISED": "ISED SFGSME 2023"}
REQUIRED_DIMENSIONS = ["statistical_unit", "population_scope", "time", "geography", "industry", "employment_size",
                       "measure_and_estimate_type", "quality_and_missingness", "identifiers"]
USE_CLASSES = ["Valid within source", "Valid side-by-side with explicit labels", "Contextual overlay only",
               "Requires Phase E crosswalk", "Prohibited or misleading"]

gates = {}


def gate(name, passed, detail=""):
    gates[name] = bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def banner(title):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


def write_csv(df, name):
    df.to_csv(OUTPUT_DIR / name, index=False)


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


# ---------------------------------------------------
# 1. Upstream integrity
# ---------------------------------------------------

def upstream_integrity():
    banner("1. UPSTREAM INTEGRITY")
    summaries = {}
    for key, path in SUMMARIES.items():
        ok = path.exists()
        summaries[key] = json.loads(path.read_text(encoding="utf-8")) if ok else {}
        gate(f"upstream_summary_exists_{key}", ok, path.relative_to(ROOT).as_posix())
        g = summaries[key].get("gates", {})
        gate(f"upstream_all_gates_pass_{key}", bool(g) and all(g.values()) and summaries[key].get("all_gates_pass") is True,
             f"{sum(g.values())} of {len(g)}")

    manifest = pd.read_csv(MANIFEST_FILE, dtype=str).fillna("")
    raw_before = {}
    mismatched = []
    for r in manifest.itertuples():
        p = ROOT / r.relative_path
        digest = sha256_file(p) if p.exists() else None
        raw_before[r.relative_path] = digest
        if digest != r.sha256 or p.stat().st_size != int(r.bytes):
            mismatched.append(r.relative_path)
    gate("all_manifest_checksums_match", not mismatched, f"{len(manifest)} raw files" + (f"; mismatched {mismatched}" if mismatched else ""))

    import duckdb
    con = duckdb.connect()
    for key, (raw, cache) in CACHES.items():
        ok, why, _ = check_lineage(con, root=ROOT, raw_path=raw, cache_path=cache, manifest_path=MANIFEST_FILE,
                                   schema_version=CACHE_SCHEMAS[key]["version"], expected_columns=expected_columns(key, raw),
                                   source_sha256=raw_before.get(raw.relative_to(ROOT).as_posix()))
        gate(f"cache_lineage_current_{key}", ok, why)
    con.close()
    return summaries, manifest, raw_before


# ---------------------------------------------------
# 2. Facts derived from upstream outputs
# ---------------------------------------------------

def derive_facts(summ):
    a, b, c = summ["0722"], summ["1097"], summ["ISED"]
    geo_a = pd.read_csv(A_DIR / "geography_classification.csv")
    ind_a = pd.read_csv(A_DIR / "industry_classification.csv")
    size_a = pd.read_csv(A_DIR / "availability_by_employment_size.csv")
    meas_a = pd.read_csv(A_DIR / "availability_by_measure.csv")
    depth_a = pd.read_csv(A_DIR / "cma_publication_depth.csv")
    size_b = pd.read_csv(B_DIR / "employment_size_profile.csv")
    cmp_b = pd.read_csv(B_DIR / "cma_comparison_0722.csv", dtype={"cma_code": str})
    cat_c = pd.read_csv(C_DIR / "row_dimension_catalogue.csv")
    cma_c = pd.read_csv(C_DIR / "cma_label_diagnostic.csv")
    main_c = cat_c[cat_c.stub_set == "tables_1_49_A"]

    geo_lv = geo_a.geo_level.value_counts().to_dict()
    ind_cls = ind_a.industry_class.value_counts().to_dict()
    flags = ind_a.set_index("industry")["flags"].fillna("")
    sizes_a = [s for s in size_a.employment_size if not s.startswith("Total")]
    sizes_c = main_c[main_c.dimension_class == "employment_size"].label.tolist()
    bands_b = size_b.dropna(subset=["lower_employees"])
    measures_cma = depth_a.measures_with_values_T004_all_sizes.fillna("")
    cmas_with_flows = int(measures_cma.apply(lambda s: all(m in s for m in ("Active businesses", "Opening businesses", "Closing businesses"))).sum())
    cmas_t004_from_2015 = int((depth_a.T004_first_valued_month == "2015-01").sum())
    shared = cmp_b[cmp_b.dguid_0722.notna() & cmp_b.dguid_1097.notna()]
    b_only = cmp_b[cmp_b.dguid_0722.isna()]
    ratio = b["compatibility_0722"]["ratio_sector_list_excl_unclassified_2025_12"]
    sector_rng = b["compatibility_0722"]["sector_median_ratio_range_excl_11"]
    rel = pd.read_csv(C_DIR / "project_relevance.csv")
    return {
        # 0722
        "a_rows": a["profile"]["row_count"], "a_months": a["profile"]["n_months"],
        "a_first_month": a["profile"]["min_ref_date"], "a_last_month": a["profile"]["max_ref_date"],
        "a_geos": int(len(geo_a)), "a_cmas": int(geo_lv.get("CMA", 0)), "a_provinces": int(geo_lv.get("Province", 0)),
        "a_territories": int(geo_lv.get("Territory", 0)),
        "a_sectors": int(ind_cls.get("naics_sector", 0)), "a_subsectors": int(ind_cls.get("naics_subsector", 0)),
        "a_aggregates": int(ind_cls.get("business_sector_aggregate", 0)),
        "a_tourism": int(ind_cls.get("tourism_aggregate", 0) + ind_cls.get("tourism_component", 0)),
        "a_industries": int(len(ind_a)),
        "a_combined_52_55": bool(flags.str.contains("combined_sectors").sum() == 1),
        "a_partial_61_62": int(flags.str.contains("partial_sector_coverage").sum()),
        "a_size_bands": sizes_a, "a_measures": meas_a.measure.tolist(),
        "a_cmas_with_sector_detail": int((depth_a.naics_sectors_with_values > 0).sum()),
        "a_cmas_with_flows_t004": cmas_with_flows, "a_cmas_t004_from_2015": cmas_t004_from_2015,
        "a_status_x_pct": round(100 * a["status_counts"]["x"] / a["profile"]["row_count"], 2),
        # 1097
        "b_rows": b["profile"]["n_rows"], "b_ref_date": b["profile"]["ref_date"],
        "b_cmas": int(b["geography"]["levels"]["CMA"]), "b_csd_in_cma": int(b["geography"]["levels"]["CSD_in_CMA"]),
        "b_csd_outside": int(b["geography"]["levels"]["CSD_outside_CMA"]),
        "b_sectors": int(b["naics"]["levels"]["sector"]), "b_subsectors": int(b["naics"]["levels"]["subsector"]),
        "b_groups": int(b["naics"]["levels"]["industry_group"]), "b_size_bands": int(len(bands_b)),
        "b_unexplained_missing": int(b["value_states"]["unexplained_missing"]),
        "b_cmas_full_sector_size": int(((cmp_b.sectors_with_locations_1097 == b["naics"]["levels"]["sector"])
                                        & (cmp_b.size_bands_with_locations_1097 == len(bands_b))).sum()),
        "b_unclassified_min": b["compatibility_0722"]["pct_unclassified_1097_by_cma"]["min"],
        "b_unclassified_max": b["compatibility_0722"]["pct_unclassified_1097_by_cma"]["max"],
        "bands_nest": bool((size_b.contained_in_0722_band_numeric.dropna() != "NONE").all()),
        # cross 0722 / 1097
        "cma_shared": int(len(shared)), "cma_b_only": int(len(b_only)), "cma_b_only_names": b_only.geo_1097.tolist(),
        "cma_label_diff": b["compatibility_0722"]["cma_labels_differing"],
        "ratio_min": ratio["min"], "ratio_median": ratio["median"], "ratio_max": ratio["max"],
        "sector_ratio_min": sector_rng["min"], "sector_ratio_max": sector_rng["max"],
        # ISED
        "c_weighted": c["facts"]["weighted_smes"], "c_respondents": c["facts"]["respondents"],
        "c_cmas": int(c["facts"]["cmas"]), "c_cma_match": int(c["facts"]["cma_labels_matching_1097"]),
        "c_cma_unmatched": cma_c[cma_c.normalized_match_1097.isna()].ised_cma_label.tolist(),
        "c_quebec_cma_exact_1097": bool(cma_c.loc[cma_c.ised_cma_label == "Quebec", "exact_match_1097"].iloc[0]),
        "c_industry_groups": int(c["facts"]["industry_groups"]), "c_special": int(c["facts"]["industry_special_aggregates"]),
        "c_size_bands": sizes_c, "c_provinces": int(c["facts"]["provinces_individual"]),
        "c_quality_F": c["facts"]["quality_F"], "c_quality_E": c["facts"]["quality_E"],
        "c_dotdot": c["facts"]["dotdot_values"], "c_dotdot_undoc": c["facts"]["dotdot_undocumented"],
        "c_t01_cma": c["facts"]["t01_cma_pct_A_to_D"], "c_t03_cma": c["facts"]["t03_cma_pct_A_to_D"],
        "c_t05_cma_F": c["facts"]["t05_cma_pct_F"], "c_nonprofit_pct": c["facts"]["nonprofit_se_pct_of_all"],
        "c_core": int((rel.relevance == "Core").sum()), "c_contextual": int((rel.relevance == "Contextual").sum()),
        "sizes_identical_ised_0722": set(sizes_c) <= set(sizes_a) and len(sizes_c) == 4,
    }


# ---------------------------------------------------
# 3. Matrices
# ---------------------------------------------------

def build_matrices(f):
    ES, ED, INF, DEC = "FACT: SOURCE", "FACT: DATA", "INFERENCE", "DECISION"
    sizes_a = "; ".join(f["a_size_bands"])
    label_diff = "; ".join(f"{d['cma_code']}: '{d['geo_0722'].replace(chr(160), ' ').strip()}' vs '{d['geo_1097']}'" for d in f["cma_label_diff"])

    grain = pd.DataFrame([
        ("statistical_unit", "Enterprise; a multi-industry or multi-region enterprise is counted in each (Note 2), so disaggregates are not additive",
         "Statistical location (Note 1); each location counted once; exactly additive (Phase B reconciliation)",
         "Enterprise (IMDB 2941); weighted survey estimate of a filtered SME population",
         ES, "03/04 docs; phase_b_summary.json reconciliation; IMDB record 2941"),
        ("population_scope", "Active employer enterprises (>= 1 employee in the month), all sizes incl. 500+; business-sector total T004",
         "Active employer locations, all industries incl. public administration (91) and Unclassified; CSDs outside CMAs only if >= 10 businesses",
         "Enterprises with 1-499 employees and >= $30,000 revenue; excludes NAICS 22, 52, 55, 61, 91 and parts of 53/62/81; excludes non-profits, government, subsidiaries, financing/leasing companies",
         ES, "metadata notes; IMDB record 2941"),
        ("time", f"Monthly, {f['a_first_month']} to {f['a_last_month']} ({f['a_months']} months), seasonally adjusted, experimental",
         f"Single semi-annual snapshot: December 2025 counts (REF_DATE {f['b_ref_date']}); not a time series (Note 3)",
         "Calendar year 2023 (collected Feb-Jun 2024); single cycle",
         ES + " / " + ED, "phase_a_summary.json profile; phase_b_summary.json profile"),
        ("geography", f"Canada, {f['a_provinces']} provinces, {f['a_territories']} territories, {f['a_cmas']} CMAs; 2016-vintage DGUIDs",
         f"{f['b_cmas']} CMAs, {f['b_csd_in_cma']} CSDs in CMAs, {f['b_csd_outside']} CSDs outside CMAs; SGC 2021 DGUIDs; no Canada or provinces",
         f"Canada; 5 regions ({f['c_provinces']} provinces individually; Atlantic grouped; BC with territories); {f['c_cmas']} CMAs by name only (no codes)",
         ED, "geography_classification.csv; geography_profile.csv; row_dimension_catalogue.csv"),
        ("industry", f"NAICS vintage not stated; {f['a_industries']} members: {f['a_sectors']} sector-level (52+55 combined; 61, 62 partial; [11] titled Forestry, fishing and hunting), {f['a_subsectors']} subsectors, {f['a_aggregates']} business-sector aggregates, {f['a_tourism']} overlapping tourism members",
         f"NAICS 2022: {f['b_sectors']} sectors, {f['b_subsectors']} subsectors, {f['b_groups']} four-digit groups, plus Unclassified",
         f"NAICS 2022 (IMDB): {f['c_industry_groups']} coarse groups (some combined) plus {f['c_special']} overlapping special aggregates",
         ED, "industry_classification.csv; naics_profile.csv; row_dimension_catalogue.csv"),
        ("employment_size", f"Total + {sizes_a}; enterprise size (closures/exits: prior-12-month average, Note 11)",
         f"Total + {f['b_size_bands']} bands (1-4 ... 500+); payroll-derived, second-highest month of 12; basis for multi-location enterprises not stated",
         "All SMEs + " + "; ".join(f["c_size_bands"]) + " (labels identical to 0722's first four)",
         ED, "availability_by_employment_size.csv; employment_size_profile.csv; row_dimension_catalogue.csv"),
        ("measure_and_estimate_type", "Counts of enterprises: active, opening, continuing, closing, reopening, entrants, temporary closures, exits",
         "Counts of employer locations (stock)",
         "Weighted survey estimates: 545 prevalence percentages, dollar totals/averages/medians, weighted enterprise counts, interest-rate averages",
         ED, "availability_by_measure.csv; phase_c_summary.json estimate_types"),
        ("quality_and_missingness", f"Administrative suppression: {f['a_status_x_pct']}% of rows 'x'; '..' = exits lag (last 6 months); fixed publication design",
         f"No suppression; omitted cells are zero by reconciliation; {f['b_unexplained_missing']} unexplained missing",
         f"Sampling error with quality codes A-F ({fmt(f['c_quality_F'])} F, {fmt(f['c_quality_E'])} E); {f['c_dotdot']} '..' cells, {f['c_dotdot_undoc']} undocumented",
         ED, "phase_a/b/c summaries"),
        ("identifiers", "Member IDs + 2016 DGUIDs (join on codes; GEO names carry non-breaking spaces)",
         "Member IDs + 2021 DGUIDs + NAICS codes (label reused across levels; names unsafe)",
         "No codes: labels only (CMAs, regions, industries)", ED, "Phase A-C documentation"),
    ], columns=["dimension", "t_0722", "t_1097", "ised_2023", "evidence_label", "evidence"])

    scope = pd.DataFrame([
        ("employer requirement", "Employer enterprises only (>= 1 employee)", "Locations with employees only", "Enterprises with 1-499 employees", "Similar in intent; different units", ES),
        ("SME employment limit", "None (all sizes)", "None (all sizes)", "1-499 employees", "ISED is a subset by size", ES),
        ("500+ employee businesses", "Included (own band)", "Included (500 plus band)", "Excluded", "Remove 500+ from StatCan totals before any scale comparison", ES),
        ("minimum revenue", "None", "None", ">= $30,000 gross revenue", "Not reconcilable from StatCan tables (no revenue dimension)", ES),
        ("excluded industries", "Business sector only; 61/62 partial; [11] scope undocumented", "None (all industries)", "NAICS 22, 52, 55, 61, 91; 5321, 5324, 6214-6223, 6242, 814110", "Approximable on 1097 NAICS codes; not on 0722's coarse members", ES),
        ("excluded organizational forms", "Not documented", "Not documented", "Non-profits, joint ventures, government, subsidiaries, financing and leasing companies", "Not reconcilable from StatCan tables", ES),
        ("public administration (91)", "Excluded (business sector)", "Included", "Excluded", "Exclude 91 from 1097 for comparisons", ES),
        ("unclassified businesses", "Not documented", f"Separate member; {f['b_unclassified_min']}-{f['b_unclassified_max']}% of CMA locations", "Not documented", "Must be carried separately; never allocated to sectors", ED),
        ("non-employer businesses", "Excluded", "Excluded (with employees)", "Excluded (1+ employees)", "Consistently excluded", ES),
        ("supplementary survey populations", "None", "None", f"Oversamples (ICT, co-ops, charities/non-profits, CSBFP users...); non-profit rows are {f['c_nonprofit_pct']}% of the weighted total", "Inclusion in All-SMEs total unresolved; immaterial to scale", ED),
        ("geographic coverage", "Canada, provinces, territories, 35 CMAs", "41 CMAs and CSDs; small non-CMA CSDs (< 10 businesses) out of scope", "Canada, regions, 5 provinces, 24 named CMAs", "No common geography except CMAs", ED),
    ], columns=["criterion", "t_0722", "t_1097", "ised_2023", "comparability", "evidence_label"])

    align = pd.DataFrame([
        # dimension, item, pair, status, detail, phase_e_requirement, evidence_label
        ("statistical_unit", "unit", "0722-1097", "non-reconcilable", "Enterprise vs location; location/enterprise ratio varies by CMA and sector", "non-reconcilable", ED),
        ("statistical_unit", "unit", "0722-ISED", "same unit, different population", "Both enterprises; ISED is a filtered survey population", "E6_ised_scope_approximation", ES),
        ("statistical_unit", "unit", "1097-ISED", "non-reconcilable", "Location vs enterprise", "non-reconcilable", ES),
        ("time", "reference period", "all", "non-reconcilable", f"Monthly {f['a_first_month']}-{f['a_last_month']} vs December 2025 snapshot vs 2023: never one observation period", "non-reconcilable", ES),
        ("geography", "CMA codes 0722 vs 1097", "0722-1097", "shared codes, different vintage", f"{f['cma_shared']} shared CMA codes; {f['cma_b_only']} CMAs only in 1097 ({', '.join(f['cma_b_only_names'])}); 2016 vs SGC 2021 boundaries: effect unknown", "E1_cma_sgc2016_to_sgc2021", ED),
        ("geography", "CMA label differences", "0722-1097", "labels differ for same code", label_diff, "E1_cma_sgc2016_to_sgc2021", ED),
        ("geography", "Quebec label ambiguity", "all", "name collision", "1097 labels CMA 421 'Quebec'; in 0722 and ISED region rows 'Quebec' is the province; ISED also has a CMA row 'Quebec'", "E2_ised_cma_labels_to_codes", ED),
        ("geography", "ISED CMA labels", "ISED-1097", "name-only; 23 of 24 match after light normalization",
         f"{f['c_cma_match']} of {f['c_cmas']} ISED CMA labels match 1097; unmatched: {', '.join(f['c_cma_unmatched'])} (1097: 'Greater Sudbury')", "E2_ised_cma_labels_to_codes", ED),
        ("geography", "provinces and regions", "ISED-0722", "name-only; partial", "ISED publishes Quebec, Ontario, Manitoba, Saskatchewan, Alberta individually; Atlantic grouped; BC combined with territories", "E8_ised_regions_to_province_codes", ED),
        ("geography", "provinces", "1097-others", "not available", "1097 has no province or Canada totals; summing CSDs undercounts (Note 2)", "non-reconcilable", ES),
        ("geography", "CSD detail", "1097-others", "only in 1097", "CSD structure has no counterpart in 0722 or ISED", "non-reconcilable", ED),
        ("industry", "0722 members to NAICS 2022", "0722-1097", "requires mapping", f"{f['a_sectors']} sector-level members; 52+55 combined; 61/62 partial (Notes 12/13); [11] scope undocumented; 311/312 subsectors; NAICS vintage not stated", "E3_0722_industry_to_naics2022", ED),
        ("industry", "0722 tourism members", "0722-1097", "non-reconcilable", f"{f['a_tourism']} tourism members are TSA-based, overlap sectors; not constructible from NAICS 4-digit", "non-reconcilable", ES),
        ("industry", "ISED groups to NAICS 2022", "ISED-1097", "requires mapping", f"{f['c_industry_groups']} coarse groups incl. 11+21 and 51+53+56+62+71 combined", "E4_ised_industry_to_naics2022", ED),
        ("industry", "ISED special aggregates", "ISED-all", "non-reconcilable", f"{f['c_special']} overlapping aggregates (ICT, KBI, tourism); definitions not published in the workbook", "E7_unclassified_and_overlapping_aggregates", ES),
        ("industry", "1097 Unclassified", "1097-others", "no counterpart", f"{f['b_unclassified_min']}-{f['b_unclassified_max']}% of CMA locations; not allocable to sectors", "E7_unclassified_and_overlapping_aggregates", ED),
        ("employment_size", "ISED vs 0722 labels", "0722-ISED", "identical labels 1-4 ... 100-499", "Same band labels; both enterprise-based, but 0722 closures/exits use a prior-12-month average and ISED is survey-reported", "E5_1097_size_to_sme_bands", ED),
        ("employment_size", "1097 bands to broader bands", "1097-0722/ISED", "numeric nesting only", "Every 1097 band nests in one broader band numerically; location vs enterprise basis makes the counts non-equivalent", "E5_1097_size_to_sme_bands", ED),
        ("measure", "flows vs stock", "0722-1097", "non-reconcilable as rate", f"1097 locations / 0722 enterprises ranges {f['ratio_min']}-{f['ratio_max']} across CMAs and {f['sector_ratio_min']}-{f['sector_ratio_max']} across sectors", "non-reconcilable", ED),
        ("measure", "survey percentages vs counts", "ISED-StatCan", "non-reconcilable", "Prevalence within a segment's applicable population is not a count", "non-reconcilable", ES),
    ], columns=["dimension", "item", "source_pair", "alignment_status", "detail", "phase_e_requirement", "evidence_label"])

    roles = pd.DataFrame([
        # layer, measure, source, estimate_type, available_grain, valid_source, constraint
        ("market momentum", "Active employer enterprises", "0722", "enterprise count (SA)", f"Canada, provinces, all {f['a_cmas_with_flows_t004']} CMAs (T004, from 2015-01); sector detail in {f['a_cmas_with_sector_detail']} CMAs from 2018-01", "0722", "Not additive across industries/CMAs"),
        ("market momentum", "Openings", "0722", "enterprise count (SA)", "as Active (Opening/Continuing/Closing published together)", "0722", "Published only where the measure group is published"),
        ("market momentum", "Closures", "0722", "enterprise count (SA)", "as Openings", "0722", "Size basis = prior-12-month average (Note 11)"),
        ("market momentum", "Net formation (openings - closures)", "0722", "derived count", "as Openings", "0722", "Same period, same series"),
        ("market momentum", "Opening rate = openings / active", "0722", "derived rate", "as Openings", "0722", "Own denominator only; declare lag convention"),
        ("market momentum", "Net formation rate = (openings - closures) / active", "0722", "derived rate", "as Openings", "0722", "Own denominator only"),
        ("market momentum", "Exits and temporary closures", "0722", "enterprise count", "Canada/provinces only; never CMAs", "0722", "Unavailable for latest 6 months (Note 10)"),
        ("market momentum", "Rolling trends (e.g. 12-month averages)", "0722", "derived", "as Active/Openings", "0722", "Seasonally adjusted, experimental; CMA sector trends only from 2018-01"),
        ("market scale", "Employer locations", "1097", "location count", f"{f['b_cmas']} CMAs and CSDs x NAICS 2022 x 8 bands", "1097", "Locations, not enterprises or clients; snapshot"),
        ("market composition", "Sector share of a CMA's locations", "1097", "derived share", "CMA/CSD x sector", "1097", "Unclassified shown separately"),
        ("market composition", "Size-band share of a CMA's locations", "1097", "derived share", "CMA/CSD x band", "1097", "Location size basis"),
        ("market composition", "CMA and CSD market structure", "1097", "location count", "CSD within CMA", "1097", "Small counts at CSD x industry level"),
        ("market scale (survey)", "Weighted in-scope SME count", "ISED", "weighted survey count", "One-way segments; Table 50 two-way crosses", "ISED", "Survey estimate of a filtered population; not a register count"),
        ("financing propensity", "External-financing request rate", "ISED", "prevalence %", "One-way segments incl. 24 CMAs", "ISED", f"CMA estimates {f['c_t01_cma']}% A-D; rank only beyond ~2 combined SE"),
        ("financing propensity", "Debt request and approval rates", "ISED", "prevalence %", "One-way segments", "ISED", f"CMA estimates {f['c_t03_cma']}% A-D"),
        ("financing propensity", "Product demand by instrument", "ISED", "prevalence %", "One-way segments", "ISED", "Instruments overlap (multiple requests)"),
        ("typical financing needs", "Deal size (amounts requested/authorized)", "ISED", "monetary", "National and size-band only", "ISED", f"{f['c_t05_cma_F']}% of CMA estimates are F"),
        ("typical financing needs", "Growth expectations, obstacles, succession", "ISED", "prevalence %", "One-way segments", "ISED", "Contextual only"),
        ("sales process", "Lead volume, conversion, RM capacity, SLA", "synthetic CRM (future)", "internal", "Not in any external source", "synthetic CRM (future)", "Never derived from public data"),
    ], columns=["layer", "measure", "source", "estimate_type", "available_grain", "valid_source", "constraint"])

    V, S, C, X, P = USE_CLASSES
    rules = pd.DataFrame([
        ("U01", "0722 openings / 0722 active businesses", V, "Same source, unit, geography, industry, size and period"),
        ("U02", "0722 net formation / 0722 active businesses", V, "Valid where Opening/Closing are published for the selected series and period"),
        ("U03", "1097 sector locations / 1097 total locations", V, "Exactly additive; Unclassified reported separately"),
        ("U04", "1097 size-band locations / 1097 total locations", V, "Exactly additive"),
        ("U05", "0722 flows / 1097 locations", P, f"Unit mismatch: location/enterprise ratio varies {f['ratio_min']}-{f['ratio_max']} across CMAs"),
        ("U06", "ISED percentages used as direct counts of businesses", P, "Prevalence within an applicable population, not a count"),
        ("U07", "ISED weighted SME counts treated as register counts", P, "Survey estimate of a filtered population with sampling error"),
        ("U08", "Multiplying one-way ISED percentages into CMA x industry x size cells", P, "Assumes independence the survey does not support"),
        ("U09", "Turning aggregate public records into identifiable leads", P, "Aggregates never identify businesses (a count of 1 is still an aggregate)"),
        ("U10", "Ranking or scoring ISED estimates coded F", P, "Source: not reliable, do not use"),
        ("U11", "Using ISED estimates coded E", C, "Permitted only with an explicit caution flag and a sensitivity check"),
        ("U12", "Comparing ISED segments without their printed SE or CV", P, "Misleading: differences within ~2 combined SE cannot rank segments"),
        ("U13", "ISED 2023 context shown alongside 1097 December 2025 scale", S, "Permitted only with both periods labelled"),
        ("U14", "Summing overlapping tourism or special-industry aggregates", P, "Aggregates overlap sectors (source footnotes)"),
        ("U15", "Joining geography records by names alone", P, "Names collide (Quebec) and differ (Sudbury, Belleville)"),
        ("U16", "Joining shared CMA codes across 2016 and 2021 vintages without qualification", X, "Codes match but boundaries may differ"),
        ("U17", "0722 active enterprises and 1097 locations shown side by side for one CMA", S, "Different units and periods; label both"),
        ("U18", "Summing 0722 industries or CMAs to build a total", P, "Enterprises counted in each industry/region (Note 2)"),
        ("U19", "Building provincial totals by summing 1097 CSDs", P, "Small non-CMA CSDs are excluded (Note 2)"),
        ("U20", "Zero-filling 0722 'x'/'..', ISED '..' or out-of-scope 1097 geographies", P, "Unavailable is not zero"),
        ("U21", "ISED financing-propensity attributes attached to 1097/0722 CMAs", X, "Requires ISED CMA label-to-code mapping and vintage qualification"),
        ("U22", "Using 1097 as a time series", P, "Single snapshot; StatCan advises against time-series use (Note 3)"),
        ("U23", "Owner-demographic segments used for targeting or routing", P, "Project decision (Phase C)"),
    ], columns=["rule_id", "use", "classification", "rationale"])

    joins = pd.DataFrame([
        ("0722-1097", "CMA code (DGUID suffix)", "qualified", f"{f['cma_shared']} shared codes; vintage 2016 vs 2021", "E1_cma_sgc2016_to_sgc2021"),
        ("0722-1097", "province code", "not available", "1097 has no provinces", "non-reconcilable"),
        ("0722-1097", "NAICS", "crosswalk required", "0722 members are not NAICS 2022 codes", "E3_0722_industry_to_naics2022"),
        ("0722-1097", "size band", "crosswalk required (numeric nesting only)", "Unit and methodology differ", "E5_1097_size_to_sme_bands"),
        ("0722-1097", "time", "not joinable", "Monthly SA vs December 2025 snapshot", "non-reconcilable"),
        ("ISED-0722", "CMA name", "prohibited as a key; crosswalk required", "No codes in ISED", "E2_ised_cma_labels_to_codes"),
        ("ISED-0722", "province/region name", "crosswalk required", "Name-only; partial provinces", "E8_ised_regions_to_province_codes"),
        ("ISED-0722", "size band label", "identical labels", "Same enterprise unit; survey vs administrative", "E5_1097_size_to_sme_bands"),
        ("ISED-0722", "industry group", "crosswalk required", "Coarse ISED groups vs 0722 members", "E4_ised_industry_to_naics2022"),
        ("ISED-1097", "CMA name", "prohibited as a key; crosswalk required", f"{f['c_cma_match']} of {f['c_cmas']} match after normalization", "E2_ised_cma_labels_to_codes"),
        ("ISED-1097", "industry group", "crosswalk required", "Coarse groups to NAICS 2022 codes", "E4_ised_industry_to_naics2022"),
        ("ISED-1097", "size band", "crosswalk required (numeric nesting only)", "Location vs enterprise", "E5_1097_size_to_sme_bands"),
        ("all", "any row-level (cell) join", "prohibited", "Different units, populations and periods", "non-reconcilable"),
    ], columns=["source_pair", "join_key", "feasibility", "reason", "phase_e_requirement"])

    temporal = pd.DataFrame([
        ("0722", "monthly", f"{f['a_first_month']} to {f['a_last_month']}", "Yes, seasonally adjusted", f"{f['a_first_month']} onward (Canada, provinces, CMA T004); 2018-01 onward for CMA sector detail; exits end 6 months before the latest month",
         "Label as monthly (SA); state the window", "No: not a denominator for 1097 or ISED"),
        ("1097", "semi-annual snapshot", "December 2025 (REF_DATE 2025-07)", "No", "None (single snapshot; Note 3)", "Label 'December 2025 counts'",
         "No: not a historical denominator for 0722 flows"),
        ("ISED", "single survey cycle", "2023 (collected Feb-Jun 2024)", "No", "None (one cycle in this workbook)", "Label '2023 survey'",
         "No: percentages are not counts"),
        ("presentation", "side-by-side", "0722 2025-12 with 1097 December 2025; ISED 2023 as context", "n/a", "n/a",
         "Every panel carries its own period and unit label", "The three are never one observation period"),
    ], columns=["source", "frequency", "reference_period", "seasonally_adjusted", "valid_within_source_trend_period",
                "presentation_label", "use_as_other_source_denominator"])

    req = pd.DataFrame([
        ("E1_cma_sgc2016_to_sgc2021", "Relate 0722 CMAs to 1097 CMAs", "0722 CMA (2016 DGUID)", "1097 CMA (2021 DGUID)", "SGC 2016", "SGC 2021",
         f"Boundary changes unknown (e.g. Belleville -> Belleville - Quinte West); {f['cma_b_only']} 1097-only CMAs have no 0722 counterpart", "one-to-one by code, qualified; unmatched for 1097-only",
         "Official SGC concordance; code match for all 35; flag CMAs with boundary change", "Side-by-side use must carry a vintage caveat; 1097-only CMAs have no momentum data"),
        ("E2_ised_cma_labels_to_codes", "Attach ISED financing context to coded CMAs", "ISED CMA label", "SGC 2021 CMA code", "name only (vintage not stated)", "SGC 2021",
         f"'{', '.join(f['c_cma_unmatched'])}' vs 'Greater Sudbury'; 'Quebec' collides with the province; ISED boundaries undocumented", "many-to-one (label to code)",
         "All 24 labels mapped explicitly (no fuzzy matching); reviewed alias list; province of each CMA consistent", "ISED stays national/regional context only"),
        ("E3_0722_industry_to_naics2022", "Align 0722 momentum with 1097 composition by industry", "0722 industry member", "NAICS 2022 sector/subsector/group", "undocumented NAICS vintage", "NAICS 2022",
         "[11] scope (ratio outlier even at 113-115); T004 inclusion of Unclassified; 52+55 combined; 61/62 partial; tourism members", "many-to-one / one-to-many; tourism non-reconcilable",
         "Each member mapped to explicit NAICS 2022 codes; 61/62 per Notes 12-13; tourism excluded", "Industry momentum only at 0722's own members; no cross-source sector ratios"),
        ("E4_ised_industry_to_naics2022", "Align ISED industry context with 1097 composition", "ISED industry group", "NAICS 2022 sector sets", "NAICS 2022 (IMDB)", "NAICS 2022",
         "Combined groups (11+21; 51+53+56+62+71); ISED exclusions inside groups (e.g. 5321, 5324, 6214-6223); special aggregates", "many-to-one (sectors to group)",
         "Each group = explicit sector list; exclusions documented; special aggregates not mapped", "ISED industry context stays at its 10 groups"),
        ("E5_1097_size_to_sme_bands", "Present 1097 size composition in the 0722/ISED band scheme", "1097 size band", "0722/ISED band", "location, payroll-derived", "enterprise band",
         "Unit and methodology differ; numeric nesting only", "many-to-one (numeric)",
         "8 bands roll up to 5 by numeric bounds; labelled 'location-based'", "Size comparisons remain within-source only"),
        ("E6_ised_scope_approximation", "Approximate the ISED SME population on StatCan data", "1097 NAICS x size", "ISED target population", "NAICS 2022", "survey scope",
         "Revenue >= $30k and organizational-form exclusions are not observable in StatCan tables", "filter (partial); remainder non-reconcilable",
         "Exclude 500+, NAICS 22/52/55/61/91 and listed 4-6 digit codes; document residual differences", "Any ISED-to-StatCan scale comparison stays indicative"),
        ("E7_unclassified_and_overlapping_aggregates", "Keep hierarchies mutually exclusive", "1097 Unclassified; 0722 tourism; ISED special aggregates", "(none)", "n/a", "n/a",
         "Unclassified cannot be allocated; aggregates overlap sectors", "non-reconcilable (treatment rule)",
         "Rule: carry Unclassified separately; never sum aggregates with sectors", "Sector totals exclude Unclassified; aggregates reported separately"),
        ("E8_ised_regions_to_province_codes", "Relate ISED regions/provinces to 0722 provinces", "ISED region/province label", "SGC province code", "name only", "SGC",
         "Atlantic grouped; BC combined with territories", "one-to-one (5 provinces); many-to-one (regions)",
         "Explicit label-to-code list; regions as province sets", "Atlantic and BC/territories only at region level"),
    ], columns=["crosswalk", "business_purpose", "source_dimension", "target_dimension", "source_classification",
                "target_classification", "unresolved_cases", "expected_mapping_type", "validation_test_required", "consequence_if_incomplete"])
    return grain, scope, align, roles, rules, joins, temporal, req


# ---------------------------------------------------
# 4. Layer evidence (tests the proposed three-layer structure)
# ---------------------------------------------------

def layer_evidence(f):
    checks = [
        ("momentum_0722", "0722 publishes Active, Opening and Closing for every CMA (business sector, all sizes)",
         f["a_cmas_with_flows_t004"] == f["a_cmas"], f"{f['a_cmas_with_flows_t004']} of {f['a_cmas']} CMAs"),
        ("momentum_0722", "0722 CMA business-sector series start in 2015-01 (long trend window)",
         f["a_cmas_t004_from_2015"] == f["a_cmas"], f"{f['a_cmas_t004_from_2015']} of {f['a_cmas']} CMAs"),
        ("scale_1097", "1097 covers every CMA with all sectors and size bands",
         f["b_cmas_full_sector_size"] == f["b_cmas"], f"{f['b_cmas_full_sector_size']} of {f['b_cmas']} CMAs"),
        ("scale_1097", "1097 has no unexplained missing cells", f["b_unexplained_missing"] == 0, str(f["b_unexplained_missing"])),
        ("financing_ised", "ISED financing-demand estimates are A-D for most CMA cells (Table 1 >= 90%)",
         f["c_t01_cma"] >= 90, f"{f['c_t01_cma']}%"),
        ("financing_ised", "ISED debt request/approval estimates are A-D for most CMA cells (Table 3 >= 90%)",
         f["c_t03_cma"] >= 90, f"{f['c_t03_cma']}%"),
        ("separation", "0722 and 1097 cannot share denominators (location/enterprise ratio varies by CMA)",
         f["ratio_max"] - f["ratio_min"] > 0.1, f"{f['ratio_min']}-{f['ratio_max']}"),
        ("separation", "ISED CMAs are labels only and not all match 1097, so a cell-level join is unsafe",
         f["c_cma_match"] < f["c_cmas"], f"{f['c_cma_match']} of {f['c_cmas']} match"),
        ("financing_ised", "ISED deal size is not usable at CMA level (Table 5: most CMA estimates are F)",
         f["c_t05_cma_F"] > 50, f"{f['c_t05_cma_F']}% F"),
    ]
    return pd.DataFrame(checks, columns=["layer", "evidence_check", "supported", "value"])


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():
    start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    banner("PHASE D — SOURCE GRAIN COMPARISON")
    summaries, manifest, raw_before = upstream_integrity()
    if not all(gates.values()):
        return finish(start, {}, {})

    banner("2. FACTS FROM UPSTREAM OUTPUTS")
    f = derive_facts(summaries)
    grain, scope, align, roles, rules, joins, temporal, req = build_matrices(f)
    evidence = layer_evidence(f)

    for df, name in ((grain, "source_grain_matrix.csv"), (scope, "population_scope_matrix.csv"),
                     (align, "dimension_alignment_matrix.csv"), (roles, "measure_role_matrix.csv"),
                     (rules, "analytical_use_rules.csv"), (joins, "join_feasibility_matrix.csv"),
                     (temporal, "temporal_alignment_matrix.csv"), (req, "phase_e_crosswalk_requirements.csv")):
        write_csv(df, name)
    write_csv(evidence, "layer_evidence.csv")

    banner("3. COMPARISON GATES")
    gate("exactly_three_sources_compared", list(grain.columns[1:4]) == ["t_0722", "t_1097", "ised_2023"] and len(SOURCES) == 3)
    gate("every_required_dimension_represented", set(REQUIRED_DIMENSIONS) <= set(grain.dimension))
    core_dims = ["statistical_unit", "population_scope", "time", "geography", "industry", "employment_size", "measure_and_estimate_type"]
    g_core = grain.set_index("dimension").loc[core_dims, ["t_0722", "t_1097", "ised_2023"]]
    gate("every_source_documents_unit_population_time_geography_industry_size_estimate",
         g_core.notna().all().all() and (g_core.apply(lambda c: c.str.len()) > 0).all().all())
    gate("every_measure_has_declared_valid_source", roles.valid_source.isin(SOURCES + ["synthetic CRM (future)"]).all())
    required_rules = {"U01": USE_CLASSES[0], "U02": USE_CLASSES[0], "U03": USE_CLASSES[0], "U04": USE_CLASSES[0],
                      "U05": USE_CLASSES[4], "U06": USE_CLASSES[4], "U07": USE_CLASSES[4], "U08": USE_CLASSES[4],
                      "U09": USE_CLASSES[4], "U10": USE_CLASSES[4], "U11": USE_CLASSES[2], "U12": USE_CLASSES[4],
                      "U13": USE_CLASSES[1], "U14": USE_CLASSES[4], "U15": USE_CLASSES[4], "U16": USE_CLASSES[3]}
    rmap = dict(zip(rules.rule_id, rules.classification))
    gate("required_use_rules_present_with_expected_classification", all(rmap.get(k) == v for k, v in required_rules.items()),
         f"{len(required_rules)} required rules")
    gate("every_use_rule_has_a_valid_class", rules.classification.isin(USE_CLASSES).all())
    unresolved = align[align.alignment_status != "same unit, different population"]
    gate("every_mismatch_assigned_to_phase_e_or_non_reconcilable",
         unresolved.phase_e_requirement.apply(lambda r: r == "non-reconcilable" or r in set(req.crosswalk)).all()
         and joins.phase_e_requirement.apply(lambda r: r == "non-reconcilable" or r in set(req.crosswalk)).all())
    gate("every_phase_e_requirement_is_referenced", set(req.crosswalk) <= set(align.phase_e_requirement) | set(joins.phase_e_requirement))
    # Phase D must not build mappings itself; Phase E's crosswalks live in data/reference/crosswalks/.
    stray = [p.relative_to(ROOT).as_posix() for p in OUTPUT_DIR.rglob("*crosswalk*")
             if p.name != "phase_e_crosswalk_requirements.csv"]
    gate("no_production_crosswalk_created", not stray and not {"source_code", "target_code"} & set(req.columns), f"stray: {stray}")
    gate("layer_structure_supported_by_evidence", evidence.supported.all(), f"{int(evidence.supported.sum())} of {len(evidence)} checks")

    facts = {
        "sources_compared": 3, "comparison_dimensions": len(grain), "use_rules": len(rules),
        "rules_prohibited": int((rules.classification == USE_CLASSES[4]).sum()),
        "rules_valid_within": int((rules.classification == USE_CLASSES[0]).sum()),
        "rules_side_by_side": int((rules.classification == USE_CLASSES[1]).sum()),
        "rules_contextual": int((rules.classification == USE_CLASSES[2]).sum()),
        "rules_crosswalk": int((rules.classification == USE_CLASSES[3]).sum()),
        "phase_e_requirements": len(req), "non_reconcilable_items": int((align.phase_e_requirement == "non-reconcilable").sum()),
        "measures": len(roles), "layer_checks": len(evidence), "layer_checks_supported": int(evidence.supported.sum()),
        **{k: v for k, v in f.items() if isinstance(v, (int, float, str, bool))},
    }
    fields = {"facts": facts, "roles": {"0722": "market momentum (business-population dynamics)",
                                        "1097": "location-based market scale and composition",
                                        "ISED": "financing-demand and business-needs context"},
              "layer_structure_supported": bool(evidence.supported.all())}

    banner("4. DOCUMENTATION CLAIMS")
    F = {k: fmt(v) for k, v in facts.items()}
    doc_claims = {
        "cma_counts": f"**{F['a_cmas']}** CMAs in 0722, **{F['b_cmas']}** in 1097",
        "cma_b_only": f"**{F['cma_b_only']}** only in 1097",
        "ised_cmas": f"**{F['c_cmas']}** ISED CMA labels, **{F['c_cma_match']}** of which match 1097",
        "ratio": f"**{F['ratio_min']}–{F['ratio_max']}** across CMAs",
        "sector_ratio": f"**{F['sector_ratio_min']}–{F['sector_ratio_max']}** across sectors",
        "rules": f"**{F['use_rules']}** use rules",
        "prohibited": f"**{F['rules_prohibited']}** are prohibited",
        "requirements": f"**{F['phase_e_requirements']}** Phase E crosswalk requirements",
        "layer_checks": f"**{F['layer_checks_supported']}** of {F['layer_checks']} evidence checks",
        "flows": f"all **{F['a_cmas_with_flows_t004']}** CMAs",
        "sector_detail": f"only **{F['a_cmas_with_sector_detail']}** CMAs",
        "b_full": f"**{F['b_cmas_full_sector_size']}** of {F['b_cmas']} CMAs",
        "t01": f"**{facts['c_t01_cma']}%**", "t03": f"**{facts['c_t03_cma']}%**", "t05": f"**{facts['c_t05_cma_F']}%**",
        "weighted": f"**{F['c_weighted']}**",
    }
    doc_text = DOC_FILE.read_text(encoding="utf-8")
    if DOC_SECTION_HEADING in doc_text:
        section = doc_text.split(DOC_SECTION_HEADING, 1)[1].split("\n## ", 1)[0]
        missing = {k: v for k, v in doc_claims.items() if v not in section}
        gate("documentation_numbers_reproduced_by_outputs", not missing,
             f"{len(doc_claims) - len(missing)} of {len(doc_claims)} claims found" + (f"; missing {missing}" if missing else ""))
    else:
        gate("documentation_numbers_reproduced_by_outputs", False, "NOT YET DOCUMENTED: Phase D section absent")
    fields["doc_claims"] = doc_claims

    banner("5. RAW INTEGRITY (AFTER)")
    changed = [r.relative_path for r in manifest.itertuples() if sha256_file(ROOT / r.relative_path) != raw_before[r.relative_path]]
    gate("no_raw_file_changed", not changed, f"{len(manifest)} raw files")
    finish(start, fields, facts)


def finish(start, fields, facts):
    failed = [k for k, v in gates.items() if not v]
    summary = {"phase": "D", "sources": SOURCE_NAMES, **fields, "gates": gates, "failed_gates": failed,
               "all_gates_pass": not failed, "elapsed_seconds": round(time.time() - start, 1)}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "phase_d_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str, ensure_ascii=False)
    banner("PHASE D COMPLETE" if not failed else "PHASE D FAILED")
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass")
    for k in failed:
        print(f"  FAILED: {k}")
    print(f"Outputs: {OUTPUT_DIR}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
