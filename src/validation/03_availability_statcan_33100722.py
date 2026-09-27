"""
Phase A — StatCan 33-10-0722-01 observation-availability validation.

Answers: why VALUE is mostly null, what each STATUS means, and whether the
missing values are structural (whole series never published / always
suppressed) or ordinary gaps.

Design:
- The 1.69 GB raw CSV is scanned into data/cache/statcan_33100722.parquet, a
  disposable speed-up, NOT a source of truth. data/raw is only ever read.
- Cache lineage (cache_lineage.py): the cache is reused only if its lineage file
  proves it was built from the raw file whose sha256 is in data/source_manifest.csv,
  with the current schema version, and is byte-identical to the cache that passed
  parity. Otherwise it is rebuilt and installed only after an independent parse of
  the raw CSV matches the cached raw fields (row count + content digest).
- The CSV is read with all_varchar + allow_quoted_nulls=false so raw strings are
  kept exactly (blank STATUS stays '' rather than being coerced to NULL).
- Parity gates must reproduce the validated Phase A baseline before any
  analysis runs. Every gate is reported; any failure exits with status 1 after
  the summary JSON is written.

Run with the Anaconda interpreter (it has duckdb):
    C:/Users/abhij/anaconda3/python.exe src/validation/03_availability_statcan_33100722.py
(works from any working directory; the project root is derived from this file's location)
"""

from pathlib import Path
import csv
import json
import re
import sys
import time

import duckdb
import pandas as pd

sys.dont_write_bytecode = True          # keep src/validation free of __pycache__ when importing the helper
from cache_lineage import CACHE_SCHEMAS, ensure_cache, expected_columns, sha256_file  # noqa: E402  (same folder)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_FILE = ROOT / "data" / "source_manifest.csv"

RAW_DIR = ROOT / "data" / "raw" / "statcan_33100722" / "33100722-eng"
CSV_FILE = RAW_DIR / "33100722.csv"
META_FILE = RAW_DIR / "33100722_MetaData.csv"

CACHE_DIR = ROOT / "data" / "cache"
CACHE_FILE = CACHE_DIR / "statcan_33100722.parquet"
SERIES_CACHE_FILE = CACHE_DIR / "statcan_33100722_series.parquet"

OUTPUT_DIR = ROOT / "data" / "validation" / "33100722"

# Cache schema (version + derived columns) is defined once in cache_lineage.CACHE_SCHEMAS.
CACHE_KEY = "statcan_33100722"

gates = {}


def gate(name, passed):
    gates[name] = bool(passed)
    return gates[name]

# Validated Phase A baseline (first full profile of the raw file, 2026-09-27; the
# superseded profiling script was removed). The cache must reproduce it exactly.
EXPECTED_ROWS = 8_575_672
EXPECTED_STATUS = {"x": 7_843_698, "": 627_970, "..": 94_108, "E": 9_896}

# Definitions quoted from the Symbol Legend in 33100722_MetaData.csv,
# except blank, which the local legend does not define. Blank is taken from
# the official StatCan "Standard table symbols" page:
# https://www.statcan.gc.ca/en/concepts/definitions/guide-symbol
STATUS_DEFINITIONS = {
    "x": ("suppressed to meet the confidentiality requirements of the Statistics Act",
          "local metadata Symbol Legend", "replaces value"),
    "..": ("not available for a specific reference period",
           "local metadata Symbol Legend", "replaces value"),
    "E": ("use with caution",
          "local metadata Symbol Legend", "accompanies value"),
    "": ("[Blank] or A-D: acceptable or better",
         "StatCan Standard table symbols page (NOT defined in local metadata)",
         "accompanies value"),
}

TERRITORY_CODES = {"60", "61", "62"}
NBSP = "\u00a0"


def banner(title):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


# ---------------------------------------------------
# 1. Parse metadata (member hierarchy + footnotes)
# ---------------------------------------------------

def read_metadata_members(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    start = next(i for i, r in enumerate(rows) if r[:2] == ["Dimension ID", "Member Name"])
    members = []
    for r in rows[start + 1:]:
        if not r or r[0] not in {"1", "2", "3", "4"}:
            break
        members.append({
            "dim_id": int(r[0]),
            "member_name_raw": r[1],
            "member_name": r[1].replace(NBSP, " ").strip(),
            "member_id": int(r[3]),
            "parent_member_id": int(r[4]) if r[4] else None,
            "member_notes": r[6],
        })
    return pd.DataFrame(members)


def classify_industries(ind):
    """Classify industry members from the metadata hierarchy, not by judgement."""
    has_children = set(ind["parent_member_id"].dropna().astype(int))
    out = []
    for _, m in ind.iterrows():
        notes = set(m["member_notes"].split(";")) if m["member_notes"] else set()
        code_match = re.search(r"\[([^\]]+)\]$", m["member_name"])
        codes = [c.strip() for c in code_match.group(1).split(",")] if code_match else []

        if "14" in notes:
            cls = "tourism_aggregate" if m["member_id"] in has_children else "tourism_component"
        elif pd.isna(m["parent_member_id"]):
            cls = "business_sector_aggregate"
        elif codes and all(re.fullmatch(r"\d{2}(-\d{2})?", c) for c in codes):
            cls = "naics_sector"
        elif codes and all(re.fullmatch(r"\d{3,6}", c) for c in codes):
            cls = "naics_subsector"
        else:
            cls = "unclassified"

        flags = []
        if cls == "naics_sector" and len(codes) > 1:
            flags.append("combined_sectors")
        if notes & {"12", "13"}:
            flags.append("partial_sector_coverage")
        if cls == "naics_subsector":
            flags.append("metadata_parent_is_T004_not_manufacturing")
        if "14" in notes:
            flags.append("not_a_naics_industry_overlaps_sectors")

        out.append({
            "industry_id": m["member_id"],
            "industry": m["member_name"],
            "parent_industry_id": None if pd.isna(m["parent_member_id"]) else int(m["parent_member_id"]),
            "industry_class": cls,
            "naics_codes": ";".join(codes),
            "metadata_notes": m["member_notes"],
            "flags": ";".join(flags),
        })
    return pd.DataFrame(out)


def classify_geographies(geo, dguids):
    """Geo level from DGUID pattern (not from names). dguids: geo_id -> DGUID."""
    out = []
    for _, m in geo.iterrows():
        dguid = dguids.get(m["member_id"], "")
        if dguid.endswith("A000011124"):
            level = "Canada"
        elif "A0002" in dguid:
            level = "Territory" if dguid[-2:] in TERRITORY_CODES else "Province"
        elif "S0503" in dguid:
            level = "CMA"
        else:
            level = "UNKNOWN"
        out.append({
            "geo_id": m["member_id"],
            "geo": m["member_name"],
            "dguid": dguid,
            "geo_level": level,
            "parent_geo_id": None if pd.isna(m["parent_member_id"]) else int(m["parent_member_id"]),
            "raw_name_has_trailing_nbsp": m["member_name_raw"].endswith(NBSP),
        })
    df = pd.DataFrame(out)
    names = dict(zip(df["geo_id"], df["geo"]))
    df["parent_geo"] = df["parent_geo_id"].map(names)
    return df


# ---------------------------------------------------
# 2. Build the cache (single scan of the raw CSV)
# ---------------------------------------------------

def build_cache(con, target):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Building cache from raw CSV (single scan): {CSV_FILE}")
    t0 = time.time()
    con.execute(f"""
    COPY (
        SELECT
            *,
            TRY_CAST(NULLIF("VALUE", '') AS DOUBLE)            AS value_num,
            CAST(split_part(COORDINATE, '.', 1) AS INTEGER)    AS geo_id,
            CAST(split_part(COORDINATE, '.', 2) AS INTEGER)    AS industry_id,
            CAST(split_part(COORDINATE, '.', 3) AS INTEGER)    AS size_id,
            CAST(split_part(COORDINATE, '.', 4) AS INTEGER)    AS measure_id,
            CAST(substr(REF_DATE, 1, 4) AS INTEGER)            AS ref_year
        FROM read_csv(
            '{CSV_FILE.as_posix()}',
            header = true,
            delim = ',',
            quote = '"',
            all_varchar = true,
            allow_quoted_nulls = false
        )
    ) TO '{target.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)
    print(f"Cache written: {target} ({target.stat().st_size / 1024**2:.0f} MB) in {time.time() - t0:.0f}s")


def finish(start, fields):
    """Print every gate, write the summary JSON (also on failure), exit 1 if any gate failed."""
    failed = [k for k, v in gates.items() if not v]
    banner("GATE RESULTS")
    for k, v in gates.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    summary = {"source": "Statistics Canada 33-10-0722-01", **fields, "gates": gates,
               "failed_gates": failed, "all_gates_pass": not failed,
               "elapsed_seconds": round(time.time() - start, 1)}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "phase_a_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    banner("PHASE A COMPLETE" if not failed else "PHASE A FAILED")
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass")
    for k in failed:
        print(f"  FAILED: {k}")
    print(f"Outputs: {OUTPUT_DIR}")
    print(f"Elapsed: {summary['elapsed_seconds']}s")
    if failed:
        sys.exit(1)


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():
    start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute("SET memory_limit = '8GB'")
    con.execute(f"SET temp_directory = '{(CACHE_DIR / 'duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order = false")

    banner("STATCAN 33-10-0722-01 — PHASE A AVAILABILITY VALIDATION")

    lineage = ensure_cache(con, root=ROOT, raw_path=CSV_FILE, cache_path=CACHE_FILE, manifest_path=MANIFEST_FILE,
                           schema_version=CACHE_SCHEMAS[CACHE_KEY]["version"], expected_columns=expected_columns(CACHE_KEY, CSV_FILE),
                           build=lambda target: build_cache(con, target))
    print(f"  cache: {lineage['action']} ({lineage.get('reason')})")
    gate("raw_source_matches_manifest", lineage["source_matches_manifest"])
    gate("cache_lineage_current_or_rebuilt_with_parity", lineage["ok"])
    lineage_fields = {"cache_lineage": {k: v for k, v in lineage.items() if k != "lineage"},
                      "cache_lineage_record": lineage.get("lineage")}
    if not lineage["ok"]:
        con.close()
        return finish(start, lineage_fields)

    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{CACHE_FILE.as_posix()}')")

    # ---------------------------------------------------
    # 3. Parity + integrity gates
    # ---------------------------------------------------
    banner("GATES")

    g = con.execute("""
    SELECT
        COUNT(*)                                                    AS row_count,
        COUNT(*) FILTER (WHERE STATUS IS NULL)                      AS status_sql_null,
        COUNT(*) FILTER (WHERE STATUS = '')                         AS status_empty_string,
        COUNT(*) FILTER (WHERE "VALUE" <> '' AND value_num IS NULL) AS value_uncastable,
        COUNT(DISTINCT COORDINATE)                                  AS n_coordinates,
        COUNT(DISTINCT VECTOR)                                      AS n_vectors,
        COUNT(DISTINCT COORDINATE || '|' || VECTOR)                 AS n_coord_vector_pairs,
        COUNT(DISTINCT COORDINATE || '|' || REF_DATE)               AS n_series_months,
        COUNT(DISTINCT REF_DATE)                                    AS n_months,
        MIN(REF_DATE) AS min_ref_date, MAX(REF_DATE) AS max_ref_date,
        COUNT(*) FILTER (WHERE SYMBOL <> '')                        AS symbol_nonblank,
        COUNT(*) FILTER (WHERE TERMINATED <> '')                    AS terminated_nonblank,
        COUNT(DISTINCT UOM || '|' || SCALAR_FACTOR || '|' || DECIMALS) AS n_unit_combos,
        ANY_VALUE(UOM || ' / ' || SCALAR_FACTOR || ' / decimals=' || DECIMALS) AS unit_combo
    FROM t
    """).df().iloc[0].to_dict()
    g = {k: (int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in g.items()}

    status_counts = dict(con.execute("SELECT STATUS, COUNT(*) FROM t GROUP BY 1").fetchall())

    gate("row_count_matches_profile", g["row_count"] == EXPECTED_ROWS)
    gate("status_counts_match_profile", status_counts == EXPECTED_STATUS)
    gate("no_sql_null_status", g["status_sql_null"] == 0)
    gate("all_nonblank_values_numeric", g["value_uncastable"] == 0)
    gate("one_vector_per_coordinate", g["n_coordinates"] == g["n_vectors"] == g["n_coord_vector_pairs"])
    gate("no_duplicate_series_months", g["n_series_months"] == g["row_count"])
    gate("single_unit_scalar_decimal", g["n_unit_combos"] == 1)

    print(f"  status counts: {status_counts}")
    print(f"  units: {g['unit_combo']}")

    if not (gates["row_count_matches_profile"] and gates["status_counts_match_profile"]):
        print("Cache does not reproduce the validated baseline. Stopping before analysis.")
        con.close()
        return finish(start, {**lineage_fields, "profile": g,
                              "status_counts": {("(blank)" if k == "" else k): v for k, v in status_counts.items()}})

    # ---------------------------------------------------
    # 4. Dimension classification from metadata
    # ---------------------------------------------------
    members = read_metadata_members(META_FILE)
    dguids = dict(con.execute("SELECT DISTINCT geo_id, DGUID FROM t").fetchall())

    geo_cls = classify_geographies(members[members.dim_id == 1], dguids)
    ind_cls = classify_industries(members[members.dim_id == 2])
    size_df = members[members.dim_id == 3][["member_id", "member_name"]].rename(
        columns={"member_id": "size_id", "member_name": "employment_size"})
    meas_df = members[members.dim_id == 4][["member_id", "member_name"]].rename(
        columns={"member_id": "measure_id", "member_name": "measure"})

    gate("geo_levels_all_classified", not (geo_cls["geo_level"] == "UNKNOWN").any())
    gate("industries_all_classified", not (ind_cls["industry_class"] == "unclassified").any())

    con.register("geo_cls", geo_cls)
    con.register("ind_cls", ind_cls)
    con.register("size_df", size_df)
    con.register("meas_df", meas_df)

    geo_cls.to_csv(OUTPUT_DIR / "geography_classification.csv", index=False)
    ind_cls.to_csv(OUTPUT_DIR / "industry_classification.csv", index=False)

    # Every coordinate in the file must resolve to a metadata member.
    orphans = con.execute("""
    SELECT COUNT(*) FROM (SELECT DISTINCT geo_id, industry_id, size_id, measure_id FROM t) s
    WHERE geo_id NOT IN (SELECT geo_id FROM geo_cls)
       OR industry_id NOT IN (SELECT industry_id FROM ind_cls)
       OR size_id NOT IN (SELECT size_id FROM size_df)
       OR measure_id NOT IN (SELECT measure_id FROM meas_df)
    """).fetchone()[0]
    gate("no_orphan_coordinates", orphans == 0)

    # ---------------------------------------------------
    # 5. Series-level table (one row per expected series in the full grid)
    # ---------------------------------------------------
    banner("SERIES-LEVEL TABLE")

    n_months = g["n_months"]

    con.execute(f"""
    CREATE TABLE series_obs AS
    WITH flagged AS (
        SELECT COORDINATE, REF_DATE, geo_id, industry_id, size_id, measure_id, STATUS, value_num,
               value_num IS NOT NULL AS is_valued,
               LAG(value_num IS NOT NULL) OVER (PARTITION BY COORDINATE ORDER BY REF_DATE) AS prev_valued
        FROM t
    )
    SELECT
        geo_id, industry_id, size_id, measure_id,
        COUNT(*)                                        AS months_present,
        COUNT(value_num)                                AS months_valued,
        COUNT(*) FILTER (WHERE STATUS = '')             AS n_blank,
        COUNT(*) FILTER (WHERE STATUS = 'E')            AS n_e,
        COUNT(*) FILTER (WHERE STATUS = 'x')            AS n_x,
        COUNT(*) FILTER (WHERE STATUS = '..')           AS n_dotdot,
        MIN(REF_DATE) FILTER (WHERE is_valued)          AS first_valued_month,
        MAX(REF_DATE) FILTER (WHERE is_valued)          AS last_valued_month,
        SUM(CASE WHEN is_valued AND NOT COALESCE(prev_valued, FALSE) THEN 1 ELSE 0 END) AS valued_runs,
        SUM(CASE WHEN prev_valued IS NOT NULL AND is_valued <> prev_valued THEN 1 ELSE 0 END) AS status_switches,
        MEDIAN(value_num)                               AS median_value,
        MIN(value_num)                                  AS min_value
    FROM flagged
    GROUP BY ALL
    """)

    con.execute(f"""
    CREATE TABLE series AS
    SELECT
        grid.geo_id, grid.industry_id, grid.size_id, grid.measure_id,
        grid.geo, grid.geo_level, grid.industry, grid.industry_class,
        grid.employment_size, grid.measure,
        COALESCE(o.months_present, 0) AS months_present,
        COALESCE(o.months_valued, 0)  AS months_valued,
        COALESCE(o.n_blank, 0) AS n_blank, COALESCE(o.n_e, 0) AS n_e,
        COALESCE(o.n_x, 0) AS n_x, COALESCE(o.n_dotdot, 0) AS n_dotdot,
        o.first_valued_month, o.last_valued_month,
        COALESCE(o.valued_runs, 0) AS valued_runs,
        COALESCE(o.status_switches, 0) AS status_switches,
        o.median_value, o.min_value,
        CASE
            WHEN o.months_present IS NULL                THEN 'absent_from_file'
            WHEN o.months_valued = 0                     THEN 'never_valued'
            WHEN o.months_valued = o.months_present      THEN 'always_valued'
            ELSE 'partially_valued'
        END AS series_class
    FROM (
        SELECT g.geo_id, g.geo, g.geo_level, i.industry_id, i.industry, i.industry_class,
               s.size_id, s.employment_size, m.measure_id, m.measure
        FROM geo_cls g CROSS JOIN ind_cls i CROSS JOIN size_df s CROSS JOIN meas_df m
    ) grid
    LEFT JOIN series_obs o USING (geo_id, industry_id, size_id, measure_id)
    """)
    con.execute(f"COPY series TO '{SERIES_CACHE_FILE.as_posix()}' (FORMAT parquet)")

    sc = con.execute(f"""
    SELECT
        COUNT(*)                                                         AS expected_series,
        COUNT(*) FILTER (WHERE months_present > 0)                       AS present_series,
        COUNT(*) FILTER (WHERE months_present > 0 AND months_present <> {n_months}) AS present_series_missing_months,
        COUNT(*) * {n_months}                                            AS expected_rows_full_grid
    FROM series
    """).df().iloc[0].to_dict()
    sc = {k: int(v) for k, v in sc.items()}
    # Not every present series has all months. That only matters if those series carry values.
    sc["valued_rows_in_series_missing_months"] = int(con.execute(f"""
        SELECT COALESCE(SUM(months_valued), 0)::BIGINT FROM series
        WHERE months_present > 0 AND months_present <> {n_months}
    """).fetchone()[0])
    gate("series_missing_months_carry_no_values", sc["valued_rows_in_series_missing_months"] == 0)
    print(f"  {sc}")

    # Are absences whole (geo, industry, size) blocks across all 8 measures?
    absent_blocks = con.execute("""
    SELECT n_measures_absent, COUNT(*) AS blocks FROM (
        SELECT geo_id, industry_id, size_id,
               COUNT(*) FILTER (WHERE series_class = 'absent_from_file') AS n_measures_absent
        FROM series GROUP BY ALL
    ) GROUP BY 1 ORDER BY 1
    """).df()
    print("  (geo, industry, size) blocks by number of measures absent:")
    print(absent_blocks.to_string(index=False))

    # ---------------------------------------------------
    # 6. Q3 — STATUS x VALUE
    # ---------------------------------------------------
    banner("Q3 — STATUS x VALUE AVAILABILITY")

    by_status = con.execute("""
    SELECT
        CASE WHEN STATUS = '' THEN '(blank)' ELSE STATUS END AS status,
        COUNT(*)                    AS total_rows,
        COUNT(value_num)            AS rows_with_value,
        COUNT(*) - COUNT(value_num) AS rows_without_value,
        ROUND(100.0 * COUNT(value_num) / COUNT(*), 2) AS pct_with_value,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_all_rows,
        STATUS AS status_raw
    FROM t GROUP BY STATUS ORDER BY total_rows DESC
    """).df()
    by_status["definition"] = by_status["status_raw"].map(lambda s: STATUS_DEFINITIONS[s][0])
    by_status["definition_source"] = by_status["status_raw"].map(lambda s: STATUS_DEFINITIONS[s][1])
    by_status["value_behaviour"] = by_status["status_raw"].map(lambda s: STATUS_DEFINITIONS[s][2])
    by_status = by_status.drop(columns="status_raw")
    by_status.to_csv(OUTPUT_DIR / "availability_by_status.csv", index=False)
    print(by_status.drop(columns=["definition", "definition_source"]).to_string(index=False))

    # ---------------------------------------------------
    # 7. Q4/Q6 — availability by dimension (from the full-grid series table)
    # ---------------------------------------------------
    banner("Q4 / Q6 — AVAILABILITY BY DIMENSION")

    def by_dimension(group_cols, order_by):
        return con.execute(f"""
        SELECT
            {group_cols},
            SUM(months_present)::BIGINT                           AS total_rows,
            SUM(months_valued)::BIGINT                            AS rows_with_value,
            (SUM(months_present) - SUM(months_valued))::BIGINT    AS rows_without_value,
            ROUND(100.0 * SUM(months_valued) / NULLIF(SUM(months_present), 0), 2) AS pct_with_value,
            COUNT(*) * {n_months}                                 AS expected_rows_full_grid,
            (COUNT(*) * {n_months} - SUM(months_present))::BIGINT AS rows_absent_from_file,
            ROUND(100.0 * SUM(months_valued) / (COUNT(*) * {n_months}), 2) AS pct_of_full_grid_with_value,
            SUM(n_blank)::BIGINT AS status_blank, SUM(n_e)::BIGINT AS status_e,
            SUM(n_x)::BIGINT AS status_x, SUM(n_dotdot)::BIGINT AS status_dotdot,
            COUNT(*)                                                    AS expected_series,
            COUNT(*) FILTER (WHERE series_class = 'always_valued')      AS series_always_valued,
            COUNT(*) FILTER (WHERE series_class = 'partially_valued')   AS series_partially_valued,
            COUNT(*) FILTER (WHERE series_class = 'never_valued')       AS series_never_valued,
            COUNT(*) FILTER (WHERE series_class = 'absent_from_file')   AS series_absent_from_file
        FROM series
        GROUP BY ALL
        ORDER BY {order_by}
        """).df()

    dims = {
        "availability_by_measure.csv": ("measure_id, measure", "measure_id"),
        "availability_by_employment_size.csv": ("size_id, employment_size", "size_id"),
        "availability_by_industry.csv": ("industry_id, industry, industry_class", "industry_class, industry_id"),
        "availability_by_geography.csv": ("geo_id, geo, geo_level", "geo_id"),
        "availability_by_geo_level.csv": ("geo_level", "geo_level"),
        "availability_by_geo_level_and_size.csv": ("geo_level, size_id, employment_size", "geo_level, size_id"),
    }
    results = {}
    for fname, (cols, order) in dims.items():
        df = by_dimension(cols, order)
        df.to_csv(OUTPUT_DIR / fname, index=False)
        results[fname] = df

    # Value floor per measure (evidence for small-count suppression; see doc).
    floor = con.execute("""
    SELECT measure_id,
           MIN(value_num) AS min_published_value,
           QUANTILE_CONT(value_num, 0.01) AS p01_published_value,
           MEDIAN(value_num) AS median_published_value,
           COUNT(*) FILTER (WHERE value_num = 0) AS zero_values
    FROM t WHERE value_num IS NOT NULL GROUP BY 1
    """).df()
    m = results["availability_by_measure.csv"].merge(floor, on="measure_id", how="left")
    m.to_csv(OUTPUT_DIR / "availability_by_measure.csv", index=False)
    results["availability_by_measure.csv"] = m

    show = ["total_rows", "rows_with_value", "pct_with_value", "status_x", "status_dotdot"]
    print("\nBy measure:")
    print(m[["measure"] + show + ["min_published_value", "zero_values"]].to_string(index=False))
    print("\nBy employment size:")
    print(results["availability_by_employment_size.csv"][["employment_size"] + show].to_string(index=False))
    print("\nBy geo level:")
    print(results["availability_by_geo_level.csv"][["geo_level", "expected_series"] + show + ["rows_absent_from_file"]].to_string(index=False))
    print("\nBy industry class:")
    print(by_dimension("industry_class", "industry_class")[["industry_class", "expected_series"] + show].to_string(index=False))

    # ---------------------------------------------------
    # 8. Q7 — time: year + month, from the row-level cache
    # ---------------------------------------------------
    banner("Q7 — AVAILABILITY OVER TIME")

    def by_time(col, name):
        return con.execute(f"""
        SELECT {col} AS {name},
               COUNT(DISTINCT REF_DATE)    AS months_in_period,
               COUNT(*)                    AS total_rows,
               COUNT(value_num)            AS rows_with_value,
               COUNT(*) - COUNT(value_num) AS rows_without_value,
               ROUND(100.0 * COUNT(value_num) / COUNT(*), 2) AS pct_with_value,
               COUNT(*) FILTER (WHERE STATUS = '')   AS status_blank,
               COUNT(*) FILTER (WHERE STATUS = 'E')  AS status_e,
               COUNT(*) FILTER (WHERE STATUS = 'x')  AS status_x,
               COUNT(*) FILTER (WHERE STATUS = '..') AS status_dotdot
        FROM t GROUP BY 1 ORDER BY 1
        """).df()

    by_year = by_time("ref_year", "ref_year")
    by_month = by_time("REF_DATE", "ref_month").drop(columns="months_in_period")
    by_year.to_csv(OUTPUT_DIR / "availability_by_year.csv", index=False)
    by_month.to_csv(OUTPUT_DIR / "availability_by_month.csv", index=False)
    print(by_year.to_string(index=False))

    months_with_values = int((by_month["rows_with_value"] > 0).sum())
    gate("every_month_has_usable_values", months_with_values == n_months)

    # '..' by measure and month — test against metadata Note 10 (no exits in last 6 months).
    dotdot = con.execute("""
    SELECT m.measure, REF_DATE AS ref_month, COUNT(*) AS rows_dotdot
    FROM t JOIN meas_df m USING (measure_id)
    WHERE STATUS = '..'
    GROUP BY ALL ORDER BY m.measure, ref_month
    """).df()
    dotdot.to_csv(OUTPUT_DIR / "unavailable_dotdot_by_measure_month.csv", index=False)

    dotdot_summary = con.execute("""
    SELECT m.measure, COUNT(*) AS rows_dotdot, COUNT(DISTINCT REF_DATE) AS months_affected,
           MIN(REF_DATE) AS first_month, MAX(REF_DATE) AS last_month
    FROM t JOIN meas_df m USING (measure_id)
    WHERE STATUS = '..' GROUP BY 1 ORDER BY rows_dotdot DESC
    """).df()
    print("\n'..' by measure:")
    print(dotdot_summary.to_string(index=False))

    # ---------------------------------------------------
    # 9. Q5 — is missingness structural?
    # ---------------------------------------------------
    banner("Q5 — STRUCTURAL vs ORDINARY MISSINGNESS")

    # Distribution of per-series share of months valued (present series only + absent bucket).
    dist = con.execute(f"""
    SELECT bucket, COUNT(*) AS series, SUM(months_present)::BIGINT AS rows_in_file, SUM(months_valued)::BIGINT AS rows_valued
    FROM (
        SELECT *, CASE
            WHEN series_class = 'absent_from_file' THEN '0_absent_from_file'
            WHEN months_valued = 0 THEN '1_valued_0pct'
            WHEN months_valued * 1.0 / months_present <= 0.25 THEN '2_valued_1_to_25pct'
            WHEN months_valued * 1.0 / months_present <= 0.50 THEN '3_valued_26_to_50pct'
            WHEN months_valued * 1.0 / months_present <= 0.75 THEN '4_valued_51_to_75pct'
            WHEN months_valued < months_present THEN '5_valued_76_to_99pct'
            ELSE '6_valued_100pct'
        END AS bucket
        FROM series
    ) GROUP BY 1 ORDER BY 1
    """).df()
    dist["pct_of_expected_series"] = (100 * dist["series"] / dist["series"].sum()).round(2)
    dist.to_csv(OUTPUT_DIR / "series_availability_distribution.csv", index=False)
    print(dist.to_string(index=False))

    # Usable-combination summary: geo level x size x industry class.
    usable = con.execute(f"""
    SELECT geo_level, size_id, employment_size, industry_class,
           COUNT(*) AS expected_series,
           COUNT(*) FILTER (WHERE series_class = 'absent_from_file')  AS absent_from_file,
           COUNT(*) FILTER (WHERE series_class = 'never_valued')      AS never_valued,
           COUNT(*) FILTER (WHERE series_class = 'partially_valued')  AS partially_valued,
           COUNT(*) FILTER (WHERE series_class = 'always_valued')     AS always_valued,
           ROUND(100.0 * COUNT(*) FILTER (WHERE series_class = 'always_valued') / COUNT(*), 1) AS pct_series_always_valued,
           ROUND(100.0 * COUNT(*) FILTER (WHERE months_valued > 0) / COUNT(*), 1)          AS pct_series_any_value,
           ROUND(100.0 * SUM(months_valued) / (COUNT(*) * {n_months}), 1)                  AS pct_full_grid_cells_valued
    FROM series GROUP BY ALL ORDER BY geo_level, size_id, industry_class
    """).df()
    usable.to_csv(OUTPUT_DIR / "usable_combinations_summary.csv", index=False)

    # Partially valued series: do they follow a few fixed publication windows,
    # or are values scattered (ordinary missingness)?
    mixed = con.execute("""
    SELECT measure_id, measure, first_valued_month, last_valued_month, months_valued,
           valued_runs AS contiguous_valued_runs,
           CASE WHEN n_x > 0 AND n_dotdot = 0 THEN 'x'
                WHEN n_dotdot > 0 AND n_x = 0 THEN '..'
                ELSE 'x and ..' END AS status_outside_window,
           COUNT(*) AS series
    FROM series WHERE series_class = 'partially_valued'
    GROUP BY ALL ORDER BY measure_id, series DESC
    """).df()
    mixed.to_csv(OUTPUT_DIR / "partially_valued_series_patterns.csv", index=False)
    print("\nPartially valued series — publication windows:")
    print(mixed.to_string(index=False))

    # Valued cells per month: a constant count within a period means a fixed
    # published series set (structural), not month-to-month random gaps.
    regimes = con.execute("""
    SELECT CASE WHEN REF_DATE < '2018-01' THEN '2015-01..2017-12'
                WHEN REF_DATE < '2025-12' THEN '2018-01..2025-11'
                ELSE '2025-12..2026-05' END AS period,
           COUNT(*) AS months, COUNT(DISTINCT valued) AS distinct_valued_counts,
           MIN(valued) AS valued_cells_per_month
    FROM (SELECT REF_DATE, COUNT(value_num) AS valued FROM t GROUP BY 1)
    GROUP BY 1 ORDER BY 1
    """).df()
    print("\nValued cells per month by period:")
    print(regimes.to_string(index=False))

    # '..' inside vs outside the Note 10 window (last six months).
    last_six_start = sorted(by_month["ref_month"])[-6]
    dotdot_window = con.execute(f"""
    SELECT CASE WHEN t.REF_DATE >= '{last_six_start}' THEN 'last_6_months' ELSE 'before_last_6_months' END AS window,
           s.series_class, COUNT(*) AS rows_dotdot
    FROM t JOIN series s USING (geo_id, industry_id, size_id, measure_id)
    WHERE t.STATUS = '..' GROUP BY ALL ORDER BY 1, 2
    """).df()
    print("\n'..' rows by Note 10 window:")
    print(dotdot_window.to_string(index=False))

    # How many measures are published per (geo, industry, size) block?
    measures_per_block = con.execute("""
    SELECT n_measures_with_values, COUNT(*) AS blocks FROM (
        SELECT geo_id, industry_id, size_id, COUNT(*) FILTER (WHERE months_valued > 0) AS n_measures_with_values
        FROM series GROUP BY ALL
    ) GROUP BY 1 ORDER BY 1
    """).df()

    # Measures are published in groups: within a block, group members are valued together.
    group_disagreements = con.execute("""
    SELECT COUNT(*) FROM (
        SELECT geo_id, industry_id, size_id,
               CASE WHEN measure_id IN (2, 3, 4) THEN 'open_cont_close'
                    WHEN measure_id IN (5, 6) THEN 'reopen_entrants'
                    WHEN measure_id IN (7, 8) THEN 'exits_tempclose' END AS measure_group,
               COUNT(DISTINCT months_valued > 0) AS states
        FROM series WHERE measure_id <> 1 GROUP BY ALL
    ) WHERE states > 1
    """).fetchone()[0]
    opening_without_active = con.execute("""
    SELECT COUNT(*) FROM (
        SELECT geo_id, industry_id, size_id,
               MAX(CASE WHEN measure_id = 1 THEN months_valued END) AS active_valued,
               MAX(CASE WHEN measure_id = 2 THEN months_valued END) AS opening_valued
        FROM series GROUP BY ALL
    ) WHERE opening_valued > 0 AND active_valued = 0
    """).fetchone()[0]
    gate("measure_groups_published_together", group_disagreements == 0)
    gate("opening_never_published_without_active", opening_without_active == 0)

    # CMA publication depth (Active businesses) — input to Phase F, not a selection.
    cma_depth = con.execute("""
    SELECT geo_id, geo,
           COUNT(DISTINCT industry_id) FILTER (WHERE months_valued > 0 AND industry_class = 'naics_sector') AS naics_sectors_with_values,
           COUNT(DISTINCT size_id)     FILTER (WHERE months_valued > 0 AND industry_id = 1)               AS size_bands_with_values_T004,
           MIN(first_valued_month)     FILTER (WHERE months_valued > 0 AND industry_id = 1)               AS T004_first_valued_month,
           MIN(first_valued_month)     FILTER (WHERE months_valued > 0 AND industry_class = 'naics_sector') AS sector_detail_first_month
    FROM series WHERE geo_level = 'CMA' AND measure_id = 1
    GROUP BY ALL ORDER BY naics_sectors_with_values DESC, size_bands_with_values_T004 DESC, geo
    """).df()
    cma_measures = con.execute("""
    SELECT geo_id, STRING_AGG(measure, '; ' ORDER BY measure_id) AS measures_with_values_T004_all_sizes
    FROM series WHERE geo_level = 'CMA' AND industry_id = 1 AND size_id = 1 AND months_valued > 0
    GROUP BY 1
    """).df()
    cma_depth = cma_depth.merge(cma_measures, on="geo_id", how="left")
    cma_depth.to_csv(OUTPUT_DIR / "cma_publication_depth.csv", index=False)
    print("\nCMA publication depth (Active businesses):")
    print(cma_depth.drop(columns=["geo_id", "measures_with_values_T004_all_sizes"]).to_string(index=False))

    # Never-valued series: which status fills them?
    never = con.execute("""
    SELECT CASE WHEN n_x = months_present THEN 'all_x'
                WHEN n_dotdot = months_present THEN 'all_dotdot'
                ELSE 'mix_x_and_dotdot' END AS never_valued_status, COUNT(*) AS series
    FROM series WHERE series_class = 'never_valued' GROUP BY 1 ORDER BY 2 DESC
    """).df()
    print("\nNever-valued series by filling status:")
    print(never.to_string(index=False))

    # Headline Canada total series must be complete.
    canada_total = con.execute("""
    SELECT measure, months_present, months_valued FROM series
    WHERE geo_level = 'Canada' AND industry_id = 1 AND size_id = 1 ORDER BY measure_id
    """).df()
    print("\nCanada / T004 / all sizes — headline series:")
    print(canada_total.to_string(index=False))

    # ---------------------------------------------------
    # 10. Summary
    # ---------------------------------------------------
    gate("valued_cells_constant_within_each_period", bool((regimes["distinct_valued_counts"] == 1).all()))
    gate("partially_valued_series_are_single_contiguous_runs", bool((mixed["contiguous_valued_runs"] == 1).all()))

    con.close()
    gate("raw_source_unchanged_after_run", sha256_file(CSV_FILE) == lineage["lineage"]["source_sha256"])
    finish(start, {
        "raw_file": CSV_FILE.relative_to(ROOT).as_posix(),
        "raw_file_bytes": CSV_FILE.stat().st_size,
        "cache_file": CACHE_FILE.relative_to(ROOT).as_posix(),
        **lineage_fields,
        "profile": g,
        "status_counts": {("(blank)" if k == "" else k): v for k, v in status_counts.items()},
        "series_grid": sc,
        "absent_blocks_by_measures_absent": absent_blocks.to_dict(orient="records"),
        "months_with_usable_values": months_with_values,
        "never_valued_by_status": never.to_dict(orient="records"),
        "valued_cells_per_month_by_period": regimes.to_dict(orient="records"),
        "dotdot_by_note10_window": dotdot_window.to_dict(orient="records"),
        "blocks_by_measures_with_values": measures_per_block.to_dict(orient="records"),
    })


if __name__ == "__main__":
    main()
