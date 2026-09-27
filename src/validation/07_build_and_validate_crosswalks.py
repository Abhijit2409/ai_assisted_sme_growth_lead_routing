"""
Phase E — build and validate the eight crosswalks required by Phase D
(data/validation/source_comparison/phase_e_crosswalk_requirements.csv).

Crosswalks align classification dimensions only. They never merge statistical units,
periods or estimate types, never use fuzzy/similarity matching, never drop an
unmatched category, and never turn a boundary-qualified match into an exact one.

Outputs:
- data/reference/crosswalks/   durable reference data (8 crosswalks + schema + sources)
- data/validation/crosswalks/  evidence that the mappings are complete and behave as documented

Every mapping decision carries an evidence source and URL (see crosswalk_sources.csv).
Explicit, reviewed mappings (e.g. ISED CMA labels) are declared in this file and then
verified against the authoritative code lists from Phases A-C. Any failed gate exits 1.

Run (Anaconda interpreter; works from any working directory):
    C:/Users/abhij/anaconda3/python.exe src/validation/07_build_and_validate_crosswalks.py
"""

from pathlib import Path
import json
import re
import sys
import time

import pandas as pd

sys.dont_write_bytecode = True          # keep src/validation free of __pycache__ when importing the helper
from cache_lineage import CACHE_SCHEMAS, check_lineage, expected_columns, sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VAL = ROOT / "data" / "validation"
REF_DIR = ROOT / "data" / "reference" / "crosswalks"
OUT_DIR = VAL / "crosswalks"
MANIFEST_FILE = ROOT / "data" / "source_manifest.csv"
DOC_FILE = ROOT / "docs" / "02_data_sources_and_validation.md"
DOC_SECTION_HEADING = "## Crosswalks (Phase E)"
REQ_FILE = VAL / "source_comparison" / "phase_e_crosswalk_requirements.csv"
RAW_0722 = ROOT / "data" / "raw" / "statcan_33100722" / "33100722-eng" / "33100722.csv"
META_0722 = ROOT / "data" / "raw" / "statcan_33100722" / "33100722-eng" / "33100722_MetaData.csv"
RAW_1097 = ROOT / "data" / "raw" / "statcan_33101097" / "33101097-eng" / "33101097.csv"
CACHE_1097 = ROOT / "data" / "cache" / "statcan_33101097.parquet"
CACHE_0722 = ROOT / "data" / "cache" / "statcan_33100722.parquet"
SUMMARIES = {"A": VAL / "33100722" / "phase_a_summary.json", "B": VAL / "33101097" / "phase_b_summary.json",
             "C": VAL / "ised_2023" / "phase_c_summary.json", "D": VAL / "source_comparison" / "phase_d_summary.json"}
ACCESS_DATE = "2026-09-27"

STATUSES = ["exact", "exact code with boundary caveat", "explicit alias", "combined category", "partial mapping",
            "target-only", "source-only", "non-reconcilable", "unresolved", "aggregate", "total"]

# ---------------------------------------------------
# Reference sources (official pages consulted; see docs)
# ---------------------------------------------------
SOURCES = [
    ("S01", "Statistics Canada", "Dictionary, Census of Population, 2021 - Census metropolitan area (CMA) and census agglomeration (CA)",
     "SGC 2021 CMA/CA delineation", "https://www12.statcan.gc.ca/census-recensement/2021/ref/dict/az/definition-eng.cfm?ID=geo009",
     "2021 Census", "E1", "Six 2016 CAs became CMAs in 2021 (Fredericton, Drummondville, Red Deer, Kamloops, Chilliwack, Nanaimo); Belleville renamed Belleville - Quinte West"),
    ("S02", "Statistics Canada", "Preliminary 2021 Census Metropolitan Area and Census Agglomeration Delineation (Table 9)",
     "CMA/CA delineation 2016 to preliminary 2021", "https://www150.statcan.gc.ca/n1/pub/92f0138m/92f0138m2019002-eng.htm",
     "Released 2019-11-13 (preliminary)", "E1", "CSDs added to existing CMAs in the preliminary 2021 delineation (evidence of possible boundary change only)"),
    ("S03", "Statistics Canada", "The Daily, 2022-02-09, Table 2: population of CMAs 2016 to 2021",
     "2021 CMA names", "https://www150.statcan.gc.ca/n1/daily-quotidien/220209/t002a-eng.htm", "2021 Census", "E1;E2",
     "Official 2021 CMA names incl. Belleville-Quinte West and Greater Sudbury"),
    ("S04", "Statistics Canada", "Table 33-10-0722-01 (metadata file)", "2016 DGUIDs; industry members; Notes 12-15",
     "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3310072201", "Downloaded file (see manifest)", "E1;E3;E7",
     "35 CMA DGUIDs (2016 vintage); 32 industry members with parents; Notes 12 (61 scope), 13 (62 scope), 14 (tourism), 15"),
    ("S05", "Statistics Canada", "Table 33-10-1097-01 (metadata file)", "SGC 2021 CMA/CSD codes; NAICS 2022 (Notes 5, 7)",
     "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3310109701", "December 2025 counts", "E1;E2;E3;E4;E5;E6;E7",
     "41 CMA codes and DGUIDs; CSD province codes; NAICS 2022 code inventory; 8 size bands; Unclassified"),
    ("S06", "Statistics Canada", "North American Industry Classification System (NAICS) Canada 2022 Version 1.0",
     "NAICS 2022 v1.0", "https://www.statcan.gc.ca/en/subjects/standard/naics/2022/v1/index", "2022 v1.0", "E3;E4;E6",
     "Authoritative NAICS 2022 structure"),
    ("S07", "Statistics Canada", "NAICS 2022 Version 1.0 - 8141 Private households - Industry group",
     "NAICS 2022 v1.0", "https://www23.statcan.gc.ca/imdb/p3VD.pl?Function=getVD&TVD=1369825&CVD=1369949&CPV=8141&CST=27012022&CLV=3&MLV=5",
     "2022 v1.0", "E6", "8141 (subsector 814) has the single child 81411 / 814110, so the 814110 exclusion is exact at 4 digits"),
    ("S08", "Statistics Canada (IMDB)", "Survey on Financing and Growth of Small and Medium Enterprises (record 2941)",
     "Survey scope; NAICS 2022", "https://www23.statcan.gc.ca/imdb/p2SV.pl?Function=getSurvey&SDDS=2941", "Reference period 2023",
     "E4;E6", "Target population and exclusions: 0 or 500+ employees; < $30,000 revenue; non-profits; joint ventures; government agencies; NAICS 22, 52, 55, 61, 91, 5321, 5324, 6214, 6215, 6219, 6221, 6222, 6223, 6242, 814110"),
    ("S09", "ISED / Statistics Canada", "Survey on Financing and Growth of SMEs 2023 - data tables (workbook)",
     "ISED row dimensions", "https://open.canada.ca/data/en/dataset/c3c9f361-c7d3-4c87-8be0-64c2b62521ee/resource/56f10b18-2aeb-4734-b0ad-8450bdf25d08",
     "2023", "E2;E4;E6;E7;E8", "ISED CMA, region and industry-group labels (group labels carry NAICS codes); special aggregates overlap sectors (footnote); subsidiaries and financing/leasing companies excluded (Notes)"),
    ("S10", "Statistics Canada", "Dictionary, Census of Population, 2021 - Table 1.8 Abbreviations and codes for provinces and territories",
     "SGC 2021 province/territory codes", "https://www12.statcan.gc.ca/census-recensement/2021/ref/dict/tab/index-eng.cfm?ID=t1_8",
     "2021 Census", "E8", "Two-digit codes 10-62 and region of each province/territory"),
    ("S11", "Statistics Canada", "Standard Geographical Classification (SGC) 2021 - Classification structure",
     "SGC 2021", "https://www23.statcan.gc.ca/imdb/p3VD.pl?Function=getVD&TVD=1346772", "SGC 2021 (released 2022-02-09)", "E8",
     "Six geographical regions: Atlantic, Quebec, Ontario, Prairies, British Columbia, Territories"),
]
SRC = {s[0]: s for s in SOURCES}

# Preliminary 2021 delineation, Table 9 (S02): CSDs added to existing CMAs (none removed).
PRELIM_CSD_ADDED = {"001": 1, "205": 2, "408": 1, "421": 1, "462": 1, "505": 6, "559": 3, "580": 1, "602": 1, "705": 1}
PROMOTED_2021 = {"320", "447", "830", "925", "930", "938"}            # S01
RENAMED_2021 = {"522": "Belleville became Belleville - Quinte West"}   # S01

# E2: reviewed explicit mapping of every ISED CMA label (no similarity matching).
# label -> (SGC 2021 CMA code, expected province code(s), review note)
ISED_CMA_REVIEWED = {
    "St. John's": ("001", "10", ""), "Halifax": ("205", "12", ""),
    "Quebec": ("421", "24", "CMA-row context (CMA Level group): Quebec CMA 421, not the province"),
    "Montreal": ("462", "24", "Reviewed alias: official label 'Montréal' (accent)"),
    "Ottawa-Gatineau": ("505", "24;35", "Reviewed alias: official label 'Ottawa - Gatineau' (spacing); spans Ontario and Quebec"),
    "Toronto": ("535", "35", ""), "Kingston": ("521", "35", ""), "Peterborough": ("529", "35", ""),
    "Hamilton": ("537", "35", ""), "Oshawa": ("532", "35", ""), "Brantford": ("543", "35", ""),
    "Guelph": ("550", "35", ""), "Barrie": ("568", "35", ""),
    "St. Catharines-Niagara": ("539", "35", "Reviewed alias: official label 'St. Catharines - Niagara' (spacing)"),
    "Kitchener-Cambridge-Waterloo": ("541", "35", "Reviewed alias: official label 'Kitchener - Cambridge - Waterloo' (spacing)"),
    "London": ("555", "35", ""), "Windsor": ("559", "35", ""),
    "Sudbury": ("580", "35", "Reviewed alias: official CMA 580 is 'Greater Sudbury / Grand Sudbury' (S02, S03); the only Sudbury CMA"),
    "Thunder Bay": ("595", "35", ""), "Winnipeg": ("602", "46", ""), "Calgary": ("825", "48", ""),
    "Edmonton": ("835", "48", ""), "Vancouver": ("933", "59", ""), "Victoria": ("935", "59", ""),
}

# E6: official ISED exclusions (S08) — exact codes, no shorthand ranges.
EXCLUDED_SECTORS = ["22", "52", "55", "61", "91"]
EXCLUDED_GROUPS = ["5321", "5324", "6214", "6215", "6219", "6221", "6222", "6223", "6242"]
EXCLUDED_6DIGIT = {"814110": "8141"}   # S07: 8141 contains only 81411 / 814110

# E8: official province/territory codes and SGC regions (S10, S11).
PROVINCES = {"10": ("Newfoundland and Labrador", "Atlantic"), "11": ("Prince Edward Island", "Atlantic"),
             "12": ("Nova Scotia", "Atlantic"), "13": ("New Brunswick", "Atlantic"), "24": ("Quebec", "Quebec"),
             "35": ("Ontario", "Ontario"), "46": ("Manitoba", "Prairies"), "47": ("Saskatchewan", "Prairies"),
             "48": ("Alberta", "Prairies"), "59": ("British Columbia", "British Columbia"), "60": ("Yukon", "Territories"),
             "61": ("Northwest Territories", "Territories"), "62": ("Nunavut", "Territories")}
ISED_REGION_REVIEWED = {
    "Atlantic": (["10", "11", "12", "13"], "combined category", "grouped region (SGC Atlantic region)"),
    "Quebec": (["24"], "exact", "individual province"),
    "Ontario": (["35"], "exact", "individual province"),
    "Northern Ontario": (["35"], "unresolved", "sub-provincial region; component definition not documented by ISED"),
    "Southern Ontario": (["35"], "unresolved", "sub-provincial region; component definition not documented by ISED"),
    "Prairies": (["46", "47", "48"], "combined category", "grouped region (SGC Prairies region)"),
    "Manitoba": (["46"], "exact", "individual province"),
    "Saskatchewan": (["47"], "exact", "individual province"),
    "Alberta": (["48"], "exact", "individual province"),
    "British Columbia and Territories": (["59", "60", "61", "62"], "combined category",
                                         "ISED grouping of British Columbia with all three territories; differs from SGC regions (BC and Territories are separate)"),
}

COMMON = ["crosswalk_id", "source_system", "source_dimension", "source_code", "source_label", "source_vintage",
          "target_system", "target_dimension", "target_code", "target_label", "target_vintage", "mapping_type",
          "mapping_status", "match_method", "boundary_change_flag", "include_flag", "exclusion_reason",
          "evidence_source", "evidence_url", "confidence", "notes"]
EXTRA = {
    "E1": ["source_dguid", "target_dguid", "preliminary_csd_additions"],
    "E2": ["ised_row_context", "official_province_codes"],
    "E3": ["source_member_id", "source_parent_id", "source_class", "sector_rollup_flag"],
    "E4": ["inclusion_type", "excluded_codes_within", "additive_flag"],
    "E5": ["source_lower", "source_upper", "target_lower", "target_upper", "unit_basis", "in_ised_scope"],
    "E6": ["rule_id", "precedence", "rule_type", "observable_in_1097", "applicability_grain", "exactness"],
    "E7": ["rule_id", "category_system", "treatment", "additive_allowed", "denominator_effect"],
    "E8": ["dimension_type", "sgc_region", "replicate_to_provinces"],
}
FILES = {"E1": "cma_sgc2016_to_sgc2021.csv", "E2": "ised_cma_to_sgc2021.csv",
         "E3": "statcan_0722_industry_to_naics2022.csv", "E4": "ised_industry_to_naics2022.csv",
         "E5": "statcan_1097_size_to_sme_bands.csv", "E6": "ised_scope_approximation_rules.csv",
         "E7": "non_additive_category_rules.csv", "E8": "ised_region_province_crosswalk.csv"}
REQ_IDS = {"E1": "E1_cma_sgc2016_to_sgc2021", "E2": "E2_ised_cma_labels_to_codes", "E3": "E3_0722_industry_to_naics2022",
           "E4": "E4_ised_industry_to_naics2022", "E5": "E5_1097_size_to_sme_bands", "E6": "E6_ised_scope_approximation",
           "E7": "E7_unclassified_and_overlapping_aggregates", "E8": "E8_ised_regions_to_province_codes"}

# Source key identifying one source member in each crosswalk (E1 target-only rows use target_code).
KEYCOL = {"E1": "source_code", "E2": "source_label", "E3": "source_member_id", "E4": "source_label", "E5": "source_code",
          "E6": "rule_id", "E7": "rule_id", "E8": "source_label"}

gates = {}
checks = []   # (area, crosswalk, check, passed, detail) -> validation CSVs


def gate(name, passed, detail=""):
    gates[name] = bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def check(area, xw, name, passed, detail=""):
    checks.append({"area": area, "crosswalk": xw, "check": name, "passed": bool(passed), "detail": detail})
    return bool(passed)


def banner(t):
    print("\n" + "=" * 90 + "\n" + t + "\n" + "=" * 90)


def row(xw, **kw):
    r = {c: "" for c in COMMON + EXTRA[xw]}
    r["crosswalk_id"] = REQ_IDS[xw]
    for k, v in kw.items():
        assert k in r, (xw, k)
        r[k] = v
    if r["evidence_source"] and not r["evidence_url"]:
        r["evidence_url"] = " ; ".join(SRC[s.strip()][4] for s in r["evidence_source"].split(";"))
    return r


def frame(xw, rows):
    return pd.DataFrame(rows, columns=COMMON + EXTRA[xw])


# ---------------------------------------------------
# Upstream integrity
# ---------------------------------------------------

def upstream():
    banner("1. UPSTREAM INTEGRITY")
    for k, p in SUMMARIES.items():
        s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        g = s.get("gates", {})
        gate(f"upstream_phase_{k}_passes", bool(g) and all(g.values()) and s.get("all_gates_pass") is True, f"{sum(g.values())} of {len(g)}")
    manifest = pd.read_csv(MANIFEST_FILE, dtype=str).fillna("")
    shas = {r.relative_path: sha256_file(ROOT / r.relative_path) for r in manifest.itertuples()}
    bad = [r.relative_path for r in manifest.itertuples() if shas[r.relative_path] != r.sha256]
    gate("raw_files_match_manifest_before", not bad, f"{len(manifest)} files")
    import duckdb
    con = duckdb.connect()
    for key, raw, cache in (("statcan_33100722", RAW_0722, CACHE_0722), ("statcan_33101097", RAW_1097, CACHE_1097)):
        ok, why, _ = check_lineage(con, root=ROOT, raw_path=raw, cache_path=cache, manifest_path=MANIFEST_FILE,
                                   schema_version=CACHE_SCHEMAS[key]["version"], expected_columns=expected_columns(key, raw),
                                   source_sha256=shas[raw.relative_to(ROOT).as_posix()])
        gate(f"cache_lineage_current_{key}", ok, why)
    return manifest, shas, con


# ---------------------------------------------------
# Crosswalk builders
# ---------------------------------------------------

def build_e1(geo_a, geo_b):
    a = geo_a[geo_a.geo_level == "CMA"].copy()
    a["code"] = a.dguid.str[-3:]
    b = geo_b[geo_b.geo_level == "CMA"].set_index("geo_code")
    rows = []
    for r in a.sort_values("code").itertuples():
        tb = b.loc[r.code] if r.code in b.index else None
        flags = ["2016 to 2021 boundaries not verified at component level"]
        if r.code in PRELIM_CSD_ADDED:
            flags.append(f"{PRELIM_CSD_ADDED[r.code]} CSD(s) added in the preliminary 2021 delineation")
        if r.code in RENAMED_2021:
            flags.append(RENAMED_2021[r.code])
        rows.append(row("E1", source_system="StatCan 33-10-0722-01", source_dimension="CMA", source_code=r.code,
                        source_label=r.geo.replace("\u00a0", " ").strip(), source_vintage="SGC 2016 (DGUID 2016)",
                        target_system="StatCan 33-10-1097-01", target_dimension="CMA",
                        target_code=r.code if tb is not None else "", target_label=tb.geo if tb is not None else "",
                        target_vintage="SGC 2021 (DGUID 2021)", mapping_type="one-to-one",
                        mapping_status="exact code with boundary caveat" if tb is not None else "source-only",
                        match_method="CMA code (DGUID suffix); names not used", boundary_change_flag="; ".join(flags),
                        include_flag=True, evidence_source="S04;S05;S01;S02",
                        confidence="high for code identity; boundary equivalence not established",
                        notes="Identity by code only; 2016 observations are not recalculated to 2021 boundaries",
                        source_dguid=r.dguid, target_dguid=tb.dguid if tb is not None else "",
                        preliminary_csd_additions=PRELIM_CSD_ADDED.get(r.code, 0)))
    for code in sorted(set(b.index) - set(a.code)):
        tb = b.loc[code]
        rows.append(row("E1", source_system="StatCan 33-10-0722-01", source_dimension="CMA", source_code="", source_label="",
                        source_vintage="SGC 2016", target_system="StatCan 33-10-1097-01", target_dimension="CMA",
                        target_code=code, target_label=tb.geo, target_vintage="SGC 2021 (DGUID 2021)",
                        mapping_type="none", mapping_status="target-only", match_method="CMA code (absent from 0722)",
                        boundary_change_flag="CA in 2016, CMA in 2021" if code in PROMOTED_2021 else "not documented",
                        include_flag=True, evidence_source="S05;S01", confidence="high",
                        notes="No momentum (0722) data for this CMA", source_dguid="", target_dguid=tb.dguid,
                        preliminary_csd_additions=""))
    return frame("E1", rows)


def build_e2(cat_c, geo_b):
    labels = cat_c[(cat_c.stub_set == "tables_1_49_A") & (cat_c.dimension_class == "geography_cma")].label.tolist()
    b = geo_b[geo_b.geo_level == "CMA"].set_index("geo_code")
    rows = []
    for lab in labels:
        rev = ISED_CMA_REVIEWED.get(lab)
        if rev is None:
            rows.append(row("E2", source_system="ISED SFGSME 2023", source_dimension="CMA (label)", source_code="",
                            source_label=lab, source_vintage="not stated", target_system="SGC 2021", target_dimension="CMA",
                            mapping_type="none", mapping_status="unresolved", match_method="no reviewed mapping",
                            include_flag=False, evidence_source="S09", confidence="none", ised_row_context="CMA Level"))
            continue
        code, prov, note = rev
        tb = b.loc[code] if code in b.index else None
        status = "exact" if tb is not None and tb.geo == lab else "explicit alias"
        rows.append(row("E2", source_system="ISED SFGSME 2023", source_dimension="CMA (label)", source_code="",
                        source_label=lab, source_vintage="not stated in the workbook", target_system="SGC 2021",
                        target_dimension="CMA", target_code=code, target_label=tb.geo if tb is not None else "",
                        target_vintage="SGC 2021", mapping_type="many-to-one (label to code)", mapping_status=status,
                        match_method="reviewed explicit mapping (no similarity matching)",
                        boundary_change_flag="ISED survey geography vintage not stated; boundaries not claimed equal to SGC 2021",
                        include_flag=True, evidence_source="S09;S05" + (";S02;S03" if lab == "Sudbury" else ""),
                        confidence="high for identity; boundary unknown", notes=note,
                        ised_row_context="CMA Level", official_province_codes=tb.province_code if tb is not None else ""))
    return frame("E2", rows), labels


def build_e3(ind_a, naics_b, notes_0722):
    codes_b = set(naics_b.naics_code.astype(str))
    rows = []
    for r in ind_a.itertuples():
        m = re.search(r"\[([^\]]+)\]$", r.industry)
        code = m.group(1) if m else ""
        base = dict(source_system="StatCan 33-10-0722-01", source_dimension="industry member", source_code=code,
                    source_label=r.industry, source_vintage="NAICS vintage not stated in 0722 metadata",
                    target_system="StatCan 33-10-1097-01", target_dimension="NAICS 2022", target_vintage="NAICS 2022",
                    source_member_id=r.industry_id,
                    source_parent_id="" if pd.isna(r.parent_industry_id) else int(r.parent_industry_id),
                    source_class=r.industry_class, evidence_source="S04;S05;S06")

        def add(target, status, mtype, rollup, notes="", conf="high", flag=""):
            rows.append(row("E3", **base, target_code=target,
                            target_label=naics_b.set_index("naics_code").naics.get(target, "") if target else "",
                            mapping_type=mtype, mapping_status=status, match_method="NAICS code in source label",
                            boundary_change_flag=flag, include_flag=rollup, sector_rollup_flag=rollup,
                            confidence=conf, notes=notes))
        cls = r.industry_class
        if cls == "business_sector_aggregate":
            add("", "aggregate", "aggregate", False, "Business-sector aggregate; not a NAICS member; Unclassified inclusion unknown")
        elif cls in ("tourism_aggregate", "tourism_component"):
            add("", "non-reconcilable", "none", False, "Tourism (TSA-based, Note 14); overlaps sectors; excluded from additive joins")
        elif cls == "naics_subsector":
            add(code, "exact", "one-to-one", False, "Subsector inside Manufacturing 31-33; never added to sector rollups")
        elif code == "11":
            for t in ("113", "114", "115"):
                add(t, "unresolved", "one-to-many", True,
                    "Member titled 'Forestry, fishing and hunting'; exact scope undocumented; Phase B ratio outlier even at 113-115",
                    conf="low")
        elif code == "52, 55":
            for t in ("52", "55"):
                add(t, "combined category", "one-to-many", True, "Combined 0722 member covers both sectors")
        elif code == "61":
            for t in ("6114", "6115", "6116", "6117"):
                add(t, "partial mapping", "one-to-many", True, "Note 12: only NAICS 6114-6117")
        elif code == "62":
            for t in ("621", "623", "624"):
                add(t, "partial mapping", "one-to-many", True, "Note 13: 621, 623, 624 (excludes 622 hospitals)")
        else:
            add(code, "exact", "one-to-one", True, "Sector code matched; 0722 NAICS vintage not stated", conf="medium-high")
    df = frame("E3", rows)
    return df, codes_b


def build_e4(cat_c, naics_b):
    main = cat_c[cat_c.stub_set == "tables_1_49_A"]
    groups = main[main.dimension_class == "industry_naics_group"]
    special = main[main.dimension_class == "industry_special_aggregate"]
    excl_within = {"53": ["5321", "5324"], "62": ["6214", "6215", "6219", "6221", "6222", "6223", "6242"], "81": ["8141 (814110)"]}
    rows = []
    for g in groups.itertuples():
        sectors = g.naics_codes.split(";")
        for s in sectors:
            partial = s in excl_within
            rows.append(row("E4", source_system="ISED SFGSME 2023", source_dimension="industry group (primary)",
                            source_code=g.naics_codes, source_label=g.label, source_vintage="NAICS 2022 (IMDB)",
                            target_system="NAICS 2022", target_dimension="sector", target_code=s,
                            target_label=naics_b.set_index("naics_code").naics.get(s, ""), target_vintage="NAICS 2022",
                            mapping_type="many-to-one (sector to group)" if len(sectors) > 1 else "one-to-one",
                            mapping_status="combined category" if len(sectors) > 1 else ("partial mapping" if partial else "exact"),
                            match_method="NAICS codes printed in the ISED label", include_flag=True,
                            evidence_source="S09;S08;S06", confidence="high",
                            notes="Values are ISED percentages for the whole group; never split to sectors",
                            inclusion_type="partial sector (survey exclusions inside)" if partial else "complete sector",
                            excluded_codes_within=";".join(excl_within.get(s, [])), additive_flag=True))
    for sp in special.itertuples():
        rows.append(row("E4", source_system="ISED SFGSME 2023", source_dimension="special aggregate", source_label=sp.label,
                        source_vintage="definition in ISED methodology report (not obtained)", target_system="NAICS 2022",
                        target_dimension="(overlapping)", mapping_type="none", mapping_status="non-reconcilable",
                        match_method="not mapped", include_flag=False, exclusion_reason="overlaps sector groups (footnote)",
                        evidence_source="S09", confidence="n/a",
                        notes="Reported separately; never part of the 10-group structure", inclusion_type="overlapping aggregate",
                        additive_flag=False))
    return frame("E4", rows), groups, special


def build_e5(size_b):
    target = {(1, 4): ("1 to 4 employees", 1, 4), (5, 9): ("5 to 19 employees", 5, 19), (10, 19): ("5 to 19 employees", 5, 19),
              (20, 49): ("20 to 99 employees", 20, 99), (50, 99): ("20 to 99 employees", 20, 99),
              (100, 199): ("100 to 499 employees", 100, 499), (200, 499): ("100 to 499 employees", 100, 499),
              (500, None): ("500 employees or more", 500, None)}
    rows = []
    for r in size_b.itertuples():
        if pd.isna(r.lower_employees):
            rows.append(row("E5", source_system="StatCan 33-10-1097-01", source_dimension="employment size", source_code=r.size_id,
                            source_label=r.employment_size, source_vintage="December 2025", target_system="0722/ISED band scheme",
                            target_dimension="employment size", target_code="TOTAL", target_label="Total, all sizes",
                            mapping_type="total", mapping_status="total", match_method="total member (not a band)",
                            include_flag=False, evidence_source="S05", confidence="high",
                            notes="Total handled separately; not a ninth band", unit_basis="location", in_ised_scope=""))
            continue
        lo, hi = int(r.lower_employees), (None if pd.isna(r.upper_employees) else int(r.upper_employees))
        lab, tlo, thi = target[(lo, hi)]
        rows.append(row("E5", source_system="StatCan 33-10-1097-01", source_dimension="employment size", source_code=r.size_id,
                        source_label=r.employment_size, source_vintage="December 2025", target_system="0722/ISED band scheme",
                        target_dimension="employment size", target_code=f"{tlo}-{thi if thi else 'plus'}", target_label=lab,
                        mapping_type="many-to-one (numeric)", mapping_status="exact" if (lo, hi) == (tlo, thi) else "combined category",
                        match_method="numeric bounds", include_flag=True, evidence_source="S05;S04", confidence="high (numeric)",
                        notes="Location-based employment-size composition; not equivalent to enterprise counts",
                        source_lower=lo, source_upper=hi if hi else "", target_lower=tlo, target_upper=thi if thi else "",
                        unit_basis="location (1097)", in_ised_scope=lo < 500))
    return frame("E5", rows)


def build_e6():
    rows, p = [], 0

    def rule(rtype, code, label, observable, grain, exactness, reason, src, notes=""):
        nonlocal p
        p += 1
        rows.append(row("E6", source_system="ISED SFGSME 2023 scope", source_dimension=rtype, source_code=code, source_label=label,
                        source_vintage="2023 survey", target_system="StatCan 33-10-1097-01", target_dimension=grain,
                        target_code=code if observable == "yes" else "", target_vintage="NAICS 2022 / December 2025",
                        mapping_type="filter", mapping_status="exact" if exactness == "exact" else ("partial mapping" if exactness == "partial" else "non-reconcilable"),
                        match_method="official code" if observable == "yes" else "not observable", include_flag=False,
                        exclusion_reason=reason, evidence_source=src, confidence="high" if exactness == "exact" else "n/a",
                        notes=notes, rule_id=f"R{p:02d}", precedence=p, rule_type=rtype, observable_in_1097=observable,
                        applicability_grain=grain, exactness=exactness))
    rule("employment size", "500+", "500 or more employees", "yes", "size band", "exact", "outside SME range", "S08",
         "1097 size is location-based: a 500+ enterprise's smaller locations remain (residual difference)")
    rule("employment size", "0", "0 employees", "yes", "table scope", "exact", "1097 covers locations with employees only", "S08;S05")
    for s in EXCLUDED_SECTORS:
        rule("industry sector", s, f"NAICS {s}", "yes", "sector", "exact", "excluded industry", "S08;S06")
    for g in EXCLUDED_GROUPS:
        rule("industry group", g, f"NAICS {g}", "yes", "4-digit industry group", "exact", "excluded industry", "S08;S06")
    for six, four in EXCLUDED_6DIGIT.items():
        rule("industry (6-digit)", six, f"NAICS {six} via {four}", "yes", "4-digit industry group", "exact",
             "excluded industry", "S08;S07", f"{four} contains only {six}")
    rule("industry", "Unclassified", "Unclassified locations", "partial", "NAICS total", "partial",
         "industry unknown: cannot test exclusions", "S05", "Carried separately (E7); not in the approximation")
    for lab in ("Gross revenue >= $30,000", "Non-profit organizations", "Joint ventures", "Government agencies",
                "Subsidiaries (workbook Notes)", "Financing and leasing companies beyond the NAICS exclusions",
                "Enterprise (not location) as the unit", "Other survey eligibility and response conditions"):
        rule("eligibility (unobservable)", "", lab, "no", "not in 1097", "unobservable", "cannot be applied to 1097",
             "S08;S09")
    return frame("E6", rows)


def build_e7(ind_a, special):
    rows, n = [], 0

    def r7(system, label, treatment, denom, src, notes=""):
        nonlocal n
        n += 1
        rows.append(row("E7", source_system=system, source_dimension="category", source_label=label,
                        mapping_type="rule", mapping_status="non-reconcilable", match_method="treatment rule",
                        include_flag=False, exclusion_reason="overlapping or unallocatable", evidence_source=src,
                        confidence="high", notes=notes, rule_id=f"N{n:02d}", category_system=system, treatment=treatment,
                        additive_allowed=False, denominator_effect=denom))
    r7("StatCan 33-10-1097-01", "Unclassified [2]", "Carry separately; never allocate proportionally to sectors",
       "Sector shares exclude Unclassified: state whether the denominator is 'Total' or 'Sub-total, classified'", "S05")
    for t in ind_a[ind_a.industry_class == "tourism_aggregate"].industry:
        r7("StatCan 33-10-0722-01", t, "Report separately; never sum with sectors", "none (not in sector denominators)", "S04")
    for t in ind_a[ind_a.industry_class == "tourism_component"].industry:
        r7("StatCan 33-10-0722-01", t, "Report separately; never sum with sectors", "none", "S04")
    for t in ind_a[ind_a.industry_class == "business_sector_aggregate"].industry:
        r7("StatCan 33-10-0722-01", t, "Use as the published total; never rebuild from members (Note 2)", "is itself a denominator", "S04")
    for t in ind_a[ind_a.industry_class == "naics_subsector"].industry:
        r7("StatCan 33-10-0722-01", t, "Nested inside Manufacturing 31-33; never add to sectors", "none", "S04",
           "Overlapping category found during construction")
    for t in special.label:
        r7("ISED SFGSME 2023", t, "Report separately; never sum with the 10 groups", "none", "S09")
    for t in ("Northern Ontario", "Southern Ontario", "Manitoba", "Saskatchewan", "Alberta"):
        r7("ISED SFGSME 2023", t, "Nested in its region row; never add to the region", "none", "S09",
           "Overlapping geography rows found during construction")
    return frame("E7", rows)


def build_e8(cat_c):
    labels = cat_c[(cat_c.stub_set == "tables_1_49_A") & (cat_c.dimension_class == "geography_region")].label.tolist()
    rows = []
    for lab in labels:
        rev = ISED_REGION_REVIEWED.get(lab)
        if rev is None:
            rows.append(row("E8", source_system="ISED SFGSME 2023", source_dimension="region/province", source_label=lab,
                            mapping_status="unresolved", match_method="no reviewed mapping", include_flag=False,
                            evidence_source="S09", confidence="none", dimension_type="unknown", replicate_to_provinces=False))
            continue
        codes, status, dtype = rev
        for c in codes:
            rows.append(row("E8", source_system="ISED SFGSME 2023", source_dimension="region/province", source_label=lab,
                            source_vintage="not stated", target_system="SGC 2021", target_dimension="province/territory",
                            target_code=c, target_label=PROVINCES[c][0], target_vintage="SGC 2021",
                            mapping_type="one-to-one" if len(codes) == 1 else "one-to-many (region membership)",
                            mapping_status=status, match_method="reviewed explicit mapping", include_flag=True,
                            evidence_source="S09;S10;S11", confidence="high" if status != "unresolved" else "low",
                            notes="Regional estimates are never replicated or split to provinces",
                            dimension_type=dtype, sgc_region=PROVINCES[c][1], replicate_to_provinces=False))
    return frame("E8", rows), labels


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():
    start = time.time()
    manifest, shas, con = upstream()
    if not all(gates.values()):
        return finish(start, {})
    REF_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    geo_a = pd.read_csv(VAL / "33100722" / "geography_classification.csv")
    ind_a = pd.read_csv(VAL / "33100722" / "industry_classification.csv")
    geo_b = pd.read_csv(VAL / "33101097" / "geography_profile.csv", dtype={"geo_code": str, "province_code": str, "parent_cma_code": str})
    naics_b = pd.read_csv(VAL / "33101097" / "naics_profile.csv", dtype={"naics_code": str, "derived_parent_code": str})
    size_b = pd.read_csv(VAL / "33101097" / "employment_size_profile.csv")
    cat_c = pd.read_csv(VAL / "ised_2023" / "row_dimension_catalogue.csv", dtype={"naics_codes": str})
    notes_0722 = META_0722.read_text(encoding="utf-8-sig")

    banner("2. BUILD CROSSWALKS")
    e1 = build_e1(geo_a, geo_b)
    e2, ised_cmas = build_e2(cat_c, geo_b)
    e3, codes_b = build_e3(ind_a, naics_b, notes_0722)
    e4, groups, special = build_e4(cat_c, naics_b)
    e5 = build_e5(size_b)
    e6 = build_e6()
    e7 = build_e7(ind_a, special)
    e8, ised_regions = build_e8(cat_c)
    xws = {"E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6, "E7": e7, "E8": e8}
    for k, df in xws.items():
        df.to_csv(REF_DIR / FILES[k], index=False, encoding="utf-8")
        print(f"  {k}: {len(df)} rows -> {FILES[k]}")
    pd.DataFrame(SOURCES, columns=["source_id", "organization", "title", "classification_or_concordance", "url",
                                   "release_or_vintage", "used_by", "claim_supported"]).assign(access_date=ACCESS_DATE) \
        .to_csv(REF_DIR / "crosswalk_sources.csv", index=False, encoding="utf-8")
    schema_rows = [{"crosswalk": k, "file": FILES[k], "column": c, "column_group": "common" if c in COMMON else "specific"}
                   for k in FILES for c in COMMON + EXTRA[k]]
    pd.DataFrame(schema_rows).to_csv(REF_DIR / "crosswalk_schema.csv", index=False, encoding="utf-8")

    # ---------------------------------------------------
    banner("3. GEOGRAPHY VALIDATION (E1, E2, E8)")
    a_cma_codes = set(geo_a[geo_a.geo_level == "CMA"].dguid.str[-3:])
    b_cmas = geo_b[geo_b.geo_level == "CMA"].set_index("geo_code")
    shared = e1[e1.mapping_status == "exact code with boundary caveat"]
    target_only = e1[e1.mapping_status == "target-only"]
    check("geography", "E1", "every 0722 CMA has one disposition", set(e1[e1.source_code != ""].source_code) == a_cma_codes
          and e1[e1.source_code != ""].source_code.is_unique, f"{len(a_cma_codes)} CMAs")
    check("geography", "E1", "shared codes represented", len(shared) == 35, str(len(shared)))
    check("geography", "E1", "1097-only CMAs represented as target-only", len(target_only) == 6
          and set(target_only.target_code) == PROMOTED_2021, ", ".join(target_only.target_label))
    check("geography", "E1", "no duplicate target codes", e1[e1.target_code != ""].target_code.is_unique)
    check("geography", "E1", "DGUID codes agree (2016 and 2021 suffix)", (shared.source_dguid.str[-3:] == shared.target_dguid.str[-3:]).all())
    check("geography", "E1", "no name-based join (codes only)", (e1.match_method.str.startswith("CMA code")).all())
    check("geography", "E1", "every shared mapping visibly boundary-flagged", (shared.boundary_change_flag.str.len() > 0).all()
          and not (e1.mapping_status == "exact").any())
    check("geography", "E1", "Belleville handled explicitly", "Belleville became Belleville - Quinte West" in
          e1.loc[e1.source_code == "522", "boundary_change_flag"].iloc[0])
    check("geography", "E2", "all 24 ISED CMA labels have exactly one disposition", len(ised_cmas) == 24 and len(e2) == 24
          and e2.source_label.is_unique)
    check("geography", "E2", "every reviewed code exists in 1097", e2.target_code.isin(b_cmas.index).all())
    check("geography", "E2", "province membership consistent", all(ISED_CMA_REVIEWED[r.source_label][1] == r.official_province_codes
                                                                   for r in e2.itertuples()))
    check("geography", "E2", "no fuzzy or similarity matching", (e2.match_method == "reviewed explicit mapping (no similarity matching)").all()
          and not re.search(r"^\s*(import|from)\s+(difflib|rapidfuzz|fuzzywuzzy|jellyfish|Levenshtein)", Path(__file__).read_text(encoding="utf-8"), re.M))
    check("geography", "E2", "reviewed mapping covers exactly the workbook labels", set(ISED_CMA_REVIEWED) == set(ised_cmas))
    check("geography", "E2", "Sudbury is an explicit alias to 580", e2.loc[e2.source_label == "Sudbury", ["target_code", "mapping_status"]].values.tolist() == [["580", "explicit alias"]])
    check("geography", "E2", "Quebec CMA row maps to CMA 421 (not the province)", e2.loc[e2.source_label == "Quebec", "target_code"].iloc[0] == "421")
    check("geography", "E2", "vintage caveat on every row", e2.boundary_change_flag.str.contains("vintage not stated").all())
    prov_a = set(geo_a[geo_a.geo_level.isin(["Province", "Territory"])].dguid.str[-2:])
    prov_b = set(c for v in geo_b.province_code.dropna() for c in v.split(";"))
    check("geography", "E8", "official province codes agree with 0722 DGUIDs and 1097 CSD codes",
          set(PROVINCES) == prov_a == prov_b, f"{len(PROVINCES)} codes")
    check("geography", "E8", "every ISED region/province label has a disposition", set(e8.source_label) == set(ised_regions)
          and set(ISED_REGION_REVIEWED) == set(ised_regions), f"{len(ised_regions)} labels")
    check("geography", "E8", "every target code valid", e8.target_code.isin(PROVINCES.keys()).all())
    singles = e8[e8.dimension_type == "individual province"]
    check("geography", "E8", "individual provinces map one-to-one", singles.groupby("source_label").size().eq(1).all()
          and singles.target_code.is_unique, f"{singles.source_label.nunique()} provinces")
    check("geography", "E8", "territories visible in BC grouping",
          set(e8[e8.source_label == "British Columbia and Territories"].target_code) == {"59", "60", "61", "62"})
    check("geography", "E8", "no regional estimate replicated to provinces", (e8.replicate_to_provinces == False).all())  # noqa: E712

    # ---------------------------------------------------
    banner("4. INDUSTRY VALIDATION (E3, E4, E7)")
    mapped3 = e3[e3.target_code != ""]
    check("industry", "E3", "all 32 source members have a disposition", e3.source_member_id.nunique() == 32 == len(ind_a))
    check("industry", "E3", "mapped codes exist in 1097 NAICS inventory", mapped3.target_code.isin(codes_b).all())
    check("industry", "E3", "52+55 maps to both sectors", set(e3[e3.source_code == "52, 55"].target_code) == {"52", "55"})
    n12 = re.search(r'^12,"?(.*)$', notes_0722, re.M).group(1)
    n13 = re.search(r'^13,"?(.*)$', notes_0722, re.M).group(1)
    check("industry", "E3", "61 follows Note 12", set(e3[e3.source_code == "61"].target_code) == set(re.findall(r"NAICS (\d{4})", n12)))
    check("industry", "E3", "62 follows Note 13", set(e3[e3.source_code == "62"].target_code) == set(re.findall(r"NAICS (\d{3})", n13)))
    check("industry", "E3", "tourism excluded from additive rollups",
          not e3[e3.source_class.isin(["tourism_aggregate", "tourism_component"])].sector_rollup_flag.any())
    check("industry", "E3", "[11] remains qualified (unresolved)", (e3[e3.source_code == "11"].mapping_status == "unresolved").all())
    rollup = e3[e3.sector_rollup_flag == True]  # noqa: E712
    check("industry", "E3", "no aggregate or subsector in sector rollup, no target counted twice",
          rollup.source_class.eq("naics_sector").all() and rollup.target_code.is_unique)
    check("industry", "E3", "no accidental duplicate member-target pairs", not e3.duplicated(["source_member_id", "target_code"]).any())
    sector_codes = set(naics_b[naics_b.naics_level == "sector"].naics_code)
    g_rows = e4[e4.source_dimension == "industry group (primary)"]
    check("industry", "E4", "all 10 primary groups mapped", g_rows.source_label.nunique() == 10 == len(groups))
    check("industry", "E4", "every mapped code is a valid 1097 sector", g_rows.target_code.isin(sector_codes).all())
    combo = {r.source_label: set(r.target_code for r in g_rows[g_rows.source_label == r.source_label].itertuples()) for r in g_rows.itertuples()}
    check("industry", "E4", "combined groups hold 11+21 and 51+53+56+62+71",
          {"11", "21"} in combo.values() and {"51", "53", "56", "62", "71"} in combo.values())
    check("industry", "E4", "no sector in two primary groups", g_rows.target_code.is_unique)
    check("industry", "E4", "groups cover exactly the in-scope sectors", set(g_rows.target_code) == sector_codes - set(EXCLUDED_SECTORS),
          f"{len(set(g_rows.target_code))} sectors")
    check("industry", "E4", "exclusions inside groups visible", set(g_rows[g_rows.excluded_codes_within != ""].target_code) == {"53", "62", "81"})
    check("industry", "E4", "special aggregates non-additive", len(special) == 8 and (e4[e4.source_dimension == "special aggregate"].additive_flag == False).all())  # noqa: E712
    nonadd = set(e7.source_label)
    check("industry", "E7", "no additive mapping includes a non-additive category",
          not set(e3[e3.sector_rollup_flag == True].source_label) & nonadd  # noqa: E712
          and not set(e4[e4.additive_flag == True].source_label) & nonadd)  # noqa: E712
    check("industry", "E7", "required categories covered",
          any("Unclassified" in s for s in nonadd) and set(ind_a[ind_a.industry_class.str.startswith("tourism")].industry) <= nonadd
          and set(ind_a[ind_a.industry_class == "business_sector_aggregate"].industry) <= nonadd and set(special.label) <= nonadd)

    # ---------------------------------------------------
    banner("5. SIZE ROLLUP (E5) AND SCOPE FILTER (E6) ON 1097")
    bands = e5[e5.mapping_status != "total"]
    check("size", "E5", "8 detailed bands covered exactly once", len(bands) == 8 and bands.source_code.is_unique)
    lo = bands.source_lower.astype(int).tolist()
    hi = [None if v == "" else int(v) for v in bands.source_upper]
    check("size", "E5", "no gaps or overlaps", lo[0] == 1 and hi[-1] is None and all(lo[i + 1] == hi[i] + 1 for i in range(7)))
    check("size", "E5", "500+ outside ISED scope", not bands[bands.source_lower.astype(int) >= 500].in_ised_scope.any())
    con.execute(f"CREATE VIEW b AS SELECT geo_id, size_id, naics_id, value_num FROM read_parquet('{CACHE_1097.as_posix()}')")
    con.register("e5", bands.assign(size_id=bands.source_code.astype(int))[["size_id", "target_label"]])
    rec = con.execute("""
        WITH tot AS (SELECT geo_id, naics_id, value_num AS total FROM b WHERE size_id = 1),
             rolled AS (SELECT geo_id, naics_id, target_label, SUM(value_num) AS v FROM b JOIN e5 USING (size_id) GROUP BY ALL),
             rsum AS (SELECT geo_id, naics_id, SUM(v) AS rolled_total FROM rolled GROUP BY ALL)
        SELECT COUNT(*) AS cells_tested, COUNT(*) FILTER (WHERE total = rolled_total) AS exact,
               MAX(ABS(total - rolled_total)) AS max_abs_diff
        FROM tot JOIN rsum USING (geo_id, naics_id)""").df().iloc[0]
    geo_ids_cma = tuple(int(x) for x in geo_b[geo_b.geo_level == "CMA"].geo_id)
    dist = con.execute(f"""
        SELECT target_label AS rolled_band, SUM(value_num)::BIGINT AS employer_locations_41_cmas
        FROM b JOIN e5 USING (size_id) WHERE naics_id = 1 AND geo_id IN {geo_ids_cma} GROUP BY 1 ORDER BY MIN(size_id)""").df()
    size_rec = pd.concat([pd.DataFrame([{"rolled_band": "(reconciliation) cells with published total", "employer_locations_41_cmas": None,
                                         "cells_tested": int(rec.cells_tested), "exact_matches": int(rec.exact), "max_abs_difference": float(rec.max_abs_diff)}]),
                          dist.assign(cells_tested=None, exact_matches=None, max_abs_difference=None)], ignore_index=True)
    size_rec["measure_label"] = "location-based employment-size composition (not an enterprise count)"
    size_rec.to_csv(OUT_DIR / "size_rollup_reconciliation.csv", index=False)
    check("size", "E5", "rolled bands sum exactly to published totals", int(rec.exact) == int(rec.cells_tested) > 0,
          f"{int(rec.exact):,} of {int(rec.cells_tested):,} cells")

    # E6 diagnostic: waterfall on 41 CMAs (sizes 1-499), using 4-digit groups as the partition of classified locations.
    groups_b = naics_b[naics_b.naics_level == "industry_group"][["naics_id", "naics_code", "derived_parent_code"]].copy()
    sub_to_sector = dict(zip(naics_b[naics_b.naics_level == "subsector"].naics_code, naics_b[naics_b.naics_level == "subsector"].derived_parent_code))
    groups_b["sector"] = groups_b.derived_parent_code.map(sub_to_sector)
    groups_b["excluded_by"] = [("sector " + s) if s in EXCLUDED_SECTORS else ("group " + g) if g in EXCLUDED_GROUPS + list(EXCLUDED_6DIGIT.values()) else ""
                               for g, s in zip(groups_b.naics_code, groups_b.sector)]
    con.register("g", groups_b)
    sizes_sme = tuple(int(x) for x in bands[bands.in_ised_scope == True].source_code)  # noqa: E712
    q = lambda sql: float(con.execute(sql).fetchone()[0] or 0)  # noqa: E731
    cma = f"geo_id IN {geo_ids_cma}"
    total_all = q(f"SELECT SUM(value_num) FROM b WHERE {cma} AND size_id = 1 AND naics_id = 1")
    sme_all = q(f"SELECT SUM(value_num) FROM b WHERE {cma} AND size_id IN {sizes_sme} AND naics_id = 1")
    unclassified_id = int(naics_b[naics_b.naics_level == "unclassified"].naics_id.iloc[0])
    uncl = q(f"SELECT SUM(value_num) FROM b WHERE {cma} AND size_id IN {sizes_sme} AND naics_id = {unclassified_id}")
    excl_sector = q(f"SELECT SUM(value_num) FROM b JOIN g USING (naics_id) WHERE {cma} AND size_id IN {sizes_sme} AND excluded_by LIKE 'sector%'")
    excl_group = q(f"SELECT SUM(value_num) FROM b JOIN g USING (naics_id) WHERE {cma} AND size_id IN {sizes_sme} AND excluded_by LIKE 'group%'")
    approx = sme_all - uncl - excl_sector - excl_group
    approx_direct = q(f"SELECT SUM(value_num) FROM b JOIN g USING (naics_id) WHERE {cma} AND size_id IN {sizes_sme} AND excluded_by = ''")
    waterfall = pd.DataFrame([
        ("employer locations, all sizes (41 CMAs)", total_all), ("excluded: 500+ band", total_all - sme_all),
        ("employer locations, 1-499 employees", sme_all), ("carried separately: Unclassified", uncl),
        ("excluded: sectors 22, 52, 55, 61, 91", excl_sector), ("excluded: 4-digit groups (5321, 5324, 6214, 6215, 6219, 6221, 6222, 6223, 6242, 8141)", excl_group),
        ("approximate ISED-scope employer locations (41 CMAs; not ISED-eligible SMEs)", approx),
    ], columns=["step", "employer_locations"])
    waterfall["employer_locations"] = waterfall.employer_locations.astype(int)
    waterfall["unit"] = "locations (1097, December 2025)"
    waterfall["not_comparable_to"] = "ISED weighted SME count (enterprises, national, revenue and form filters, 2023)"
    rules6 = e6
    check("scope", "E6", "every official exclusion has a rule",
          set(EXCLUDED_SECTORS + EXCLUDED_GROUPS + list(EXCLUDED_6DIGIT)) <= set(rules6.source_code))
    check("scope", "E6", "precedence deterministic and unique", rules6.precedence.is_unique and rules6.rule_id.is_unique)
    check("scope", "E6", "exact, partial and unobservable rules distinguished", set(rules6.exactness) == {"exact", "partial", "unobservable"})
    check("scope", "E6", "500+ band excluded", "500+" in set(rules6[rules6.exactness == "exact"].source_code))
    check("scope", "E6", "unobservable eligibility rules present", (rules6.observable_in_1097 == "no").sum() >= 7)
    check("scope", "E6", "every excluded 4-digit code exists in 1097", set(EXCLUDED_GROUPS + list(EXCLUDED_6DIGIT.values())) <= set(groups_b.naics_code))
    check("scope", "E6", "filter arithmetic reconciles (waterfall = sum of included groups)", abs(approx - approx_direct) < 0.5,
          f"{int(approx):,} vs {int(approx_direct):,}")
    check("scope", "E6", "approximation never labelled as ISED-eligible SMEs",
          waterfall.step.str.contains("not ISED-eligible").any() and not waterfall.step.str.contains("eligible SMEs$").any())
    scope_val = pd.concat([rules6[["rule_id", "precedence", "rule_type", "source_code", "source_label", "observable_in_1097",
                                   "applicability_grain", "exactness"]].rename(columns={"source_code": "code", "source_label": "label"}),
                           waterfall.rename(columns={"step": "label"}).assign(rule_type="diagnostic waterfall")], ignore_index=True)
    scope_val.to_csv(OUT_DIR / "scope_filter_validation.csv", index=False)

    # ---------------------------------------------------
    banner("6. INVENTORY, COVERAGE, CARDINALITY, UNRESOLVED")
    req = pd.read_csv(REQ_FILE)
    inv = pd.DataFrame([{"crosswalk": k, "requirement_id": REQ_IDS[k], "file": f"data/reference/crosswalks/{FILES[k]}",
                         "rows": len(df), "columns": len(df.columns), "schema_matches": list(df.columns) == COMMON + EXTRA[k]}
                        for k, df in xws.items()])
    inv.to_csv(OUT_DIR / "crosswalk_inventory.csv", index=False)
    expected_sources = {"E1": 35 + 6, "E2": 24, "E3": 32, "E4": 18, "E5": 9, "E6": len(e6), "E7": len(e7), "E8": 10}
    cov = []
    for k, df in xws.items():
        key = df.source_code.where(df.source_code != "", df.target_code) if k == "E1" else df[KEYCOL[k]]
        sc = df.mapping_status.value_counts().to_dict()
        cov.append({"crosswalk": k, "source_members_expected": expected_sources[k], "source_members_with_disposition": int(key.nunique()),
                    **{f"status_{s}": int(sc.get(s, 0)) for s in STATUSES}})
    cov = pd.DataFrame(cov)
    cov.to_csv(OUT_DIR / "crosswalk_coverage.csv", index=False)
    card = []
    for k, df in xws.items():
        keycol = KEYCOL[k]
        if k == "E1":   # target-only rows have no 0722 code: key them by their 1097 code
            df = df.assign(source_code=df.source_code.where(df.source_code != "", "target-only:" + df.target_code))
        per = df.groupby(keycol).size()
        declared_multi = df[df.mapping_type.str.contains("one-to-many|many-to-one|region membership", regex=True)]
        dup = df.duplicated([keycol, "target_code"]).sum()
        card.append({"crosswalk": k, "rows": len(df), "source_keys": int(per.size), "max_targets_per_source": int(per.max()),
                     "sources_with_multiple_targets": int((per > 1).sum()),
                     "multi_target_sources_declared": bool(set(per[per > 1].index) <= set(declared_multi[keycol])),
                     "accidental_duplicates": int(dup), "mapping_types": "; ".join(f"{t}={n}" for t, n in df.mapping_type.value_counts().items())})
    card = pd.DataFrame(card)
    card.to_csv(OUT_DIR / "crosswalk_cardinality.csv", index=False)
    visible = []
    for k, df in xws.items():
        for r in df[df.mapping_status.isin(["target-only", "source-only", "non-reconcilable", "unresolved", "partial mapping"])].itertuples():
            visible.append({"crosswalk": k, "mapping_status": r.mapping_status, "source_label": r.source_label, "target_code": r.target_code,
                            "target_label": r.target_label, "reason": r.notes or r.exclusion_reason or r.boundary_change_flag})
    visible = pd.DataFrame(visible)
    visible.to_csv(OUT_DIR / "unmatched_and_unresolved.csv", index=False)
    chk = pd.DataFrame(checks)
    chk[chk.area.isin(["industry"])].to_csv(OUT_DIR / "industry_mapping_validation.csv", index=False)
    chk[chk.area.isin(["geography"])].to_csv(OUT_DIR / "geography_mapping_validation.csv", index=False)
    chk[chk.area.isin(["size", "scope"])].to_csv(OUT_DIR / "crosswalk_behaviour_checks.csv", index=False)
    for c in checks:
        print(f"    [{'ok' if c['passed'] else 'NO'}] {c['crosswalk']}: {c['check']}" + (f" ({c['detail']})" if c["detail"] else ""))

    banner("7. GATES")
    gate("all_eight_requirements_implemented", set(req.crosswalk) == set(REQ_IDS.values()) and all((REF_DIR / f).exists() for f in FILES.values()))
    gate("every_crosswalk_has_explicit_schema", inv.schema_matches.all())
    gate("every_status_in_documented_vocabulary", all(df.mapping_status.isin(STATUSES).all() for df in xws.values()))
    gate("all_source_members_have_disposition", (cov.source_members_with_disposition == cov.source_members_expected).all())
    gate("every_mapping_has_evidence_url", all((df.evidence_url.str.startswith("https://")).all() for df in xws.values()))
    gate("no_fuzzy_matching", all(c["passed"] for c in checks if "fuzzy" in c["check"]))
    gate("all_target_codes_valid", all(c["passed"] for c in checks if "valid" in c["check"] or "exist" in c["check"]))
    gate("one_to_many_declared_and_no_accidental_duplicates", card.multi_target_sources_declared.all() and (card.accidental_duplicates == 0).all())
    for area, xw_list in (("geography", ["E1", "E2", "E8"]), ("industry", ["E3", "E4", "E7"]), ("size", ["E5"]), ("scope", ["E6"])):
        for xw in xw_list:
            sub = [c for c in checks if c["crosswalk"] == xw]
            gate(f"{xw}_validation_checks_pass", all(c["passed"] for c in sub), f"{sum(c['passed'] for c in sub)} of {len(sub)}")
    gate("unresolved_mappings_remain_visible", len(visible) > 0 and set(visible.mapping_status) >= {"unresolved", "target-only", "non-reconcilable"})

    facts = {
        "e1_shared": int(len(shared)), "e1_target_only": int(len(target_only)),
        "e1_prelim_csd_changed": int((shared.preliminary_csd_additions.astype(int) > 0).sum()),
        "e2_labels": int(len(e2)), "e2_exact": int((e2.mapping_status == "exact").sum()),
        "e2_alias": int((e2.mapping_status == "explicit alias").sum()),
        "e3_members": int(e3.source_member_id.nunique()), "e3_rows": int(len(e3)),
        "e3_exact": int(e3[e3.mapping_status == "exact"].source_member_id.nunique()),
        "e3_rollup_members": int(rollup.source_member_id.nunique()),
        "e3_nonreconcilable": int(e3[e3.mapping_status == "non-reconcilable"].source_member_id.nunique()),
        "e4_groups": int(g_rows.source_label.nunique()), "e4_sectors": int(g_rows.target_code.nunique()), "e4_special": int(len(special)),
        "e5_cells": int(rec.cells_tested), "e5_exact": int(rec.exact),
        "e6_rules": int(len(e6)), "e6_exact": int((e6.exactness == "exact").sum()), "e6_unobservable": int((e6.exactness == "unobservable").sum()),
        "e6_total_all": int(total_all), "e6_sme": int(sme_all), "e6_unclassified": int(uncl), "e6_approx": int(approx),
        "e6_approx_pct_of_sme": round(100 * approx / sme_all, 1),
        "e7_rules": int(len(e7)), "e8_labels": int(len(ised_regions)), "e8_rows": int(len(e8)),
        "e8_unresolved": int(e8[e8.mapping_status == "unresolved"].source_label.nunique()),
        "visible_unresolved_rows": int(len(visible)), "checks": len(checks), "checks_passed": int(sum(c["passed"] for c in checks)),
        "sources": len(SOURCES),
    }

    banner("8. DOCUMENTATION AND RAW INTEGRITY")
    F = {k: (f"{v:,}" if isinstance(v, int) else str(v)) for k, v in facts.items()}
    claims = {
        "e1": f"**{F['e1_shared']}** shared CMA codes mapped as exact code with boundary caveat, **{F['e1_target_only']}** target-only",
        "e1_prelim": f"**{F['e1_prelim_csd_changed']}** of the shared CMAs had CSDs added",
        "e2": f"**{F['e2_labels']}** ISED labels: **{F['e2_exact']}** exact, **{F['e2_alias']}** explicit aliases",
        "e3": f"**{F['e3_members']}** members in **{F['e3_rows']}** rows",
        "e3_rollup": f"**{F['e3_rollup_members']}** sector-level members",
        "e3_nr": f"**{F['e3_nonreconcilable']}** tourism members",
        "e4": f"**{F['e4_groups']}** groups cover **{F['e4_sectors']}** sectors",
        "e5": f"**{F['e5_exact']}** of {F['e5_cells']} cells",
        "e6": f"**{F['e6_rules']}** rules: **{F['e6_exact']}** exact",
        "e6_unobs": f"**{F['e6_unobservable']}** unobservable",
        "e6_approx": f"**{F['e6_approx']}** approximate ISED-scope employer locations",
        "e6_pct": f"**{F['e6_approx_pct_of_sme']}%** of the 1–499-employee locations",
        "e7": f"**{F['e7_rules']}** non-additive rules",
        "e8": f"**{F['e8_labels']}** ISED region and province labels",
        "e8_unres": f"**{F['e8_unresolved']}** unresolved",
        "sources": f"**{F['sources']}** official references",
        "checks": f"**{F['checks_passed']}** of {F['checks']} crosswalk checks",
    }
    doc = DOC_FILE.read_text(encoding="utf-8")
    if DOC_SECTION_HEADING in doc:
        section = doc.split(DOC_SECTION_HEADING, 1)[1].split("\n## ", 1)[0]
        missing = {k: v for k, v in claims.items() if v not in section}
        gate("documentation_numbers_reproduced_by_outputs", not missing,
             f"{len(claims) - len(missing)} of {len(claims)} claims" + (f"; missing {missing}" if missing else ""))
    else:
        gate("documentation_numbers_reproduced_by_outputs", False, "NOT YET DOCUMENTED: Phase E section absent")
    changed = [r.relative_path for r in manifest.itertuples() if sha256_file(ROOT / r.relative_path) != shas[r.relative_path]]
    gate("no_raw_file_changed", not changed, f"{len(manifest)} files")
    con.close()
    finish(start, {"facts": facts, "doc_claims": claims,
                   "crosswalk_files": {k: f"data/reference/crosswalks/{v}" for k, v in FILES.items()}})


def finish(start, fields):
    failed = [k for k, v in gates.items() if not v]
    summary = {"phase": "E", **fields, "gates": gates, "failed_gates": failed, "all_gates_pass": not failed,
               "elapsed_seconds": round(time.time() - start, 1)}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "phase_e_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str, ensure_ascii=False)
    banner("PHASE E COMPLETE" if not failed else "PHASE E FAILED")
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass")
    for k in failed:
        print(f"  FAILED: {k}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
