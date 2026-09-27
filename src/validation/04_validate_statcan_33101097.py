"""
Phase B — StatCan 33-10-1097-01 validation (Canadian Business Counts, with employees,
CMAs and CSDs, December 2025).

Question: can this table supply a reliable employer-business denominator at the
geography, NAICS and employment-size levels the project needs?

Design (mirrors 03_availability_statcan_33100722.py):
- data/raw is read-only. The raw files are checked against data/source_manifest.csv
  (sha256) before AND after processing, and against the members of the original
  downloaded zip.
- The raw CSV is scanned into data/cache/statcan_33101097.parquet, a disposable
  speed-up (gitignored), not a source of truth. Raw strings are kept exactly
  (all_varchar, allow_quoted_nulls=false). Cache lineage (cache_lineage.py): the
  cache is reused only if its lineage proves it was built from the manifest's raw
  checksum with the current schema and is unchanged since parity; otherwise it is
  rebuilt and verified before use.
- Parity: an independent parser (Python csv module) re-reads the raw CSV, and an
  order-independent hash of every row (all 16 raw fields) is compared with the cache.
- Compatibility checks read the Phase A cache (data/cache/statcan_33100722.parquet).
  Its lineage must be current (built from the manifest's raw checksum, Phase A schema,
  unchanged since parity); otherwise run 03_availability_statcan_33100722.py first.
- Metadata notes are parsed from the raw note lines, not csv.reader fields: the notes
  contain unescaped quotation marks (HTML links), so csv.reader splits them and the
  old parser truncated Note 1 at "For example,".
- Every numeric claim in the Phase B section of docs/02_data_sources_and_validation.md
  is written to data/validation/33101097/. Any failed gate exits with status 1.

Run order (Anaconda interpreter, which has duckdb):
    C:/Users/abhij/anaconda3/python.exe src/validation/00_build_source_manifest.py
    C:/Users/abhij/anaconda3/python.exe src/validation/03_availability_statcan_33100722.py
    C:/Users/abhij/anaconda3/python.exe src/validation/04_validate_statcan_33101097.py
(works from any working directory; the project root is derived from this file's location)
"""

from pathlib import Path
import csv
import hashlib
import json
import re
import sys
import time
import zipfile

import duckdb
import pandas as pd

sys.dont_write_bytecode = True          # keep src/validation free of __pycache__ when importing the helper
from cache_lineage import CACHE_SCHEMAS, check_lineage, ensure_cache, expected_columns  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = ROOT / "data" / "raw" / "statcan_33101097" / "33101097-eng"
ZIP_FILE = RAW_DIR.parent / "33101097-eng.zip"
CSV_FILE = RAW_DIR / "33101097.csv"
META_FILE = RAW_DIR / "33101097_MetaData.csv"
MANIFEST_FILE = ROOT / "data" / "source_manifest.csv"

CACHE_DIR = ROOT / "data" / "cache"
CACHE_FILE = CACHE_DIR / "statcan_33101097.parquet"
CACHE_0722 = CACHE_DIR / "statcan_33100722.parquet"
RAW_0722 = ROOT / "data" / "raw" / "statcan_33100722" / "33100722-eng" / "33100722.csv"
EXPECTED_0722_ROWS = 8_575_672

OUTPUT_DIR = ROOT / "data" / "validation" / "33101097"

NAICS_COL = "North American Industry Classification System (NAICS)"
RAW_COLUMNS = [
    "REF_DATE", "GEO", "DGUID", "Employment size", NAICS_COL,
    "UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID", "VECTOR", "COORDINATE",
    "VALUE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS",
]

GROUPING_ROOT_NAME = "Other Census Subdivisions"
DGUID_PREFIX = {"CMA": "2021S0503", "CSD_in_CMA": "2021A0005", "CSD_outside_CMA": "2021A0005"}

# NAICS 2022: 3-digit subsectors whose sector is a two-digit range.
RANGE_SECTORS = {"31": "31-33", "32": "31-33", "33": "31-33",
                 "44": "44-45", "45": "44-45", "48": "48-49", "49": "48-49"}

# DIAGNOSTIC ONLY — not a production crosswalk (Phase E builds those).
# 0722 industry members (keyed by the code in their label) expressed as 1097 NAICS codes:
#   [11] is titled "Forestry, fishing and hunting" in 0722 -> 113, 114, 115 (exact scope undocumented)
#   [52, 55] is one combined 0722 member
#   [61] per 0722 metadata Note 12 -> 6114-6117;  [62] per 0722 Note 13 -> 621, 623, 624
SECTOR_MAP_0722 = {
    "11": ["113", "114", "115"], "21": ["21"], "22": ["22"], "23": ["23"],
    "31-33": ["31-33"], "41": ["41"], "44-45": ["44-45"], "48-49": ["48-49"],
    "51": ["51"], "52, 55": ["52", "55"], "53": ["53"], "54": ["54"], "56": ["56"],
    "61": ["6114", "6115", "6116", "6117"], "62": ["621", "623", "624"],
    "71": ["71"], "72": ["72"], "81": ["81"],
}

# Blank STATUS is not defined in the local legend; definition from
# https://www.statcan.gc.ca/en/concepts/definitions/guide-symbol
BLANK_STATUS_DEFINITION = "[Blank] or A-D: acceptable or better"

gates = {}


def banner(title):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


def gate(name, passed, detail=""):
    gates[name] = bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def row_digest(fields):
    """Order-independent multiset digest contribution for one row of raw strings."""
    joined = "\x1f".join(fields).encode("utf-8")
    return int.from_bytes(hashlib.sha256(joined).digest()[:8], "big")


def to_py(d):
    """numpy scalars -> Python; integral floats -> int (for clean CSV/JSON)."""
    out = {}
    for k, v in d.items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        out[k] = v
    return out


def bands_contiguous(size):
    """Bands start at 1, each lower bound = previous upper + 1, last band open-ended."""
    b = size.dropna(subset=["lower_employees"]).sort_values("lower_employees")
    lo, hi = b.lower_employees.tolist(), b.upper_employees.tolist()
    return (lo[0] == 1 and pd.isna(hi[-1]) and not any(pd.isna(h) for h in hi[:-1])
            and all(lo[i + 1] == hi[i] + 1 for i in range(len(lo) - 1)))


def write_csv(df, name):
    """Write to OUTPUT_DIR, keeping integral float columns (NaN-padded counts) as integers."""
    df = df.copy()
    for c in df.columns:
        if df[c].dtype.kind == "f":
            s = df[c].dropna()
            if len(s) and (s == s.round()).all():
                df[c] = df[c].astype("Int64")
    df.to_csv(OUTPUT_DIR / name, index=False)


# ---------------------------------------------------
# 1. Raw integrity: manifest + original zip
# ---------------------------------------------------

def check_raw_against_manifest(stage):
    if not MANIFEST_FILE.exists():
        raise SystemExit(f"Missing {MANIFEST_FILE}. Run 00_build_source_manifest.py first.")
    manifest = pd.read_csv(MANIFEST_FILE, dtype=str)
    ok, detail = True, []
    for path in (CSV_FILE, META_FILE, ZIP_FILE):
        rel = path.relative_to(ROOT).as_posix()
        row = manifest[manifest["relative_path"] == rel]
        if row.empty:
            ok = False
            detail.append(f"{rel} not in manifest")
            continue
        match = (path.stat().st_size == int(row.iloc[0]["bytes"])
                 and sha256_file(path) == row.iloc[0]["sha256"])
        ok &= match
        detail.append(f"{path.name}: {'match' if match else 'DIFFERS'}")
    gate(f"raw_files_match_manifest_{stage}", ok, "; ".join(detail))


def zip_member_matches(member_name, extracted_path):
    with zipfile.ZipFile(ZIP_FILE) as z:
        info = z.getinfo(member_name)
        h = hashlib.sha256()
        with z.open(info) as m:
            while block := m.read(1 << 20):
                h.update(block)
    return info.file_size == extracted_path.stat().st_size and h.hexdigest() == sha256_file(extracted_path)


# ---------------------------------------------------
# 2. Metadata
# ---------------------------------------------------

def read_metadata(path):
    raw_bytes = path.read_bytes()
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    cube = dict(zip(rows[0], rows[1]))

    dim_start = next(i for i, r in enumerate(rows) if r[:2] == ["Dimension ID", "Dimension name"])
    dims = {}
    for r in rows[dim_start + 1:]:
        if not r or not r[0].isdigit():
            break
        dims[int(r[0])] = {"name": r[1], "notes": r[2]}

    start = next(i for i, r in enumerate(rows) if r[:2] == ["Dimension ID", "Member Name"])
    members = []
    for r in rows[start + 1:]:
        if not r or not r[0].isdigit():
            break
        members.append({
            "dim_id": int(r[0]),
            "member_name": r[1],
            "classification_code": r[2].strip("[]"),
            "member_id": int(r[3]),
            "metadata_parent_id": int(r[4]) if r[4] else None,
            "terminated": r[5],
            "member_notes": r[6],
        })

    notes = read_notes(path)

    legend_start = next(i for i, r in enumerate(rows) if r[:1] == ["Symbol Legend"])
    legend = {}
    for r in rows[legend_start + 2:]:
        if not r or len(r) < 2:
            break
        legend[r[1]] = r[0]

    survey_start = next(i for i, r in enumerate(rows) if r[:2] == ["Survey Code", "Survey Name"])
    survey = f"{rows[survey_start + 1][0]} {rows[survey_start + 1][1]}"

    return {
        "cube": cube, "dims": dims, "members": pd.DataFrame(members), "notes": notes["clean"],
        "notes_raw": notes["raw"], "note_links": notes["links"], "notes_csv_fields": notes["csv_fields"],
        "notes_csv_second_field": notes["csv_second_field"],
        "legend": legend, "survey": survey, "raw_bytes": raw_bytes,
    }


def read_notes(path):
    """Notes from the raw text lines of the metadata file.

    Each note is one line: <id>,"<text>". The text contains HTML links whose
    quotation marks are not escaped for CSV, so csv.reader splits the line into
    extra fields (and field 2 alone truncates Note 1). The logical note is the
    whole remainder of the line after "<id>,".
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('"Note ID"') or line.startswith("Note ID"))
    out = {"raw": {}, "clean": {}, "links": {}, "csv_fields": {}, "csv_second_field": {}}
    for line in lines[start + 1:]:
        m = re.match(r"^(\d+),(.*)$", line)
        if not m:
            break
        nid, body = m.groups()
        text = body[1:-1] if len(body) > 1 and body.startswith('"') and body.endswith('"') else body
        text = text.replace('""', '"')
        out["raw"][nid] = body
        out["links"][nid] = re.findall(r'href="([^"]+)"', text)
        out["clean"][nid] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
        fields = next(csv.reader([line]))
        out["csv_fields"][nid] = len(fields)
        out["csv_second_field"][nid] = fields[1] if len(fields) > 1 else ""
    return out


def classify_geography(members):
    geo = members[members.dim_id == 1].copy()
    root_ids = set(geo.loc[geo.member_name == GROUPING_ROOT_NAME, "member_id"])

    def level(r):
        if r.member_id in root_ids:
            return "grouping_root"
        if pd.isna(r.metadata_parent_id):
            return "CMA"
        return "CSD_outside_CMA" if r.metadata_parent_id in root_ids else "CSD_in_CMA"

    geo["geo_level"] = geo.apply(level, axis=1)
    geo["expected_dguid"] = [
        DGUID_PREFIX[lv] + c if lv in DGUID_PREFIX else None
        for lv, c in zip(geo.geo_level, geo.classification_code)
    ]
    geo["province_code"] = [c[:2] if lv.startswith("CSD") else None
                            for lv, c in zip(geo.geo_level, geo.classification_code)]
    names = dict(zip(geo.member_id, geo.member_name))
    codes = dict(zip(geo.member_id, geo.classification_code))
    geo["parent_cma_code"] = [codes.get(p) if lv == "CSD_in_CMA" else None
                              for p, lv in zip(geo.metadata_parent_id, geo.geo_level)]
    geo["parent_cma"] = [names.get(p) if lv == "CSD_in_CMA" else None
                         for p, lv in zip(geo.metadata_parent_id, geo.geo_level)]

    # CMA province codes come from their CSDs (Ottawa - Gatineau spans two provinces).
    cma_prov = (geo[geo.geo_level == "CSD_in_CMA"].groupby("metadata_parent_id")["province_code"]
                .agg(lambda s: ";".join(sorted(set(s)))))
    geo.loc[geo.geo_level == "CMA", "province_code"] = geo.loc[geo.geo_level == "CMA", "member_id"].map(cma_prov)

    label_counts = geo.member_name.value_counts()
    geo["label_shared_by_members"] = geo.member_name.map(label_counts).astype(int)
    geo["label_equals_parent_cma_label"] = geo.member_name == geo.parent_cma
    return geo.rename(columns={"member_id": "geo_id", "member_name": "geo",
                               "classification_code": "geo_code"})


def classify_naics(members):
    """Levels from metadata structure + NAICS codes, not labels.

    Root = member without a parent. Its two children are split by metadata Note 6
    ("The Unclassified category is composed of units which have not been coded to an
    industry"): the child carrying Note 6 is 'unclassified', the other is the parent of
    every coded member ('classified_subtotal'). Below that, metadata parent IDs are flat,
    so sector/subsector/industry-group levels come from the NAICS code structure.
    """
    na = members[members.dim_id == 3].copy()
    root_id = int(na.loc[na.metadata_parent_id.isna(), "member_id"].iloc[0])
    root_children = na[na.metadata_parent_id == root_id]
    unclassified_id = int(root_children.loc[root_children.member_notes.str.split(";").apply(lambda n: "6" in n), "member_id"].iloc[0])
    classified_id = int(root_children.loc[root_children.member_id != unclassified_id, "member_id"].iloc[0])

    def level(r):
        if r.member_id == root_id:
            return "table_total"
        if r.member_id == unclassified_id:
            return "unclassified"
        if r.member_id == classified_id:
            return "classified_subtotal"
        c = r.classification_code
        if re.fullmatch(r"\d{2}(-\d{2})?", c):
            return "sector"
        if re.fullmatch(r"\d{3}", c):
            return "subsector"
        if re.fullmatch(r"\d{4}", c):
            return "industry_group"
        return "unclassified_code_shape"

    na["naics_level"] = na.apply(level, axis=1)
    by_code = {c: m for c, m, lv in zip(na.classification_code, na.member_id, na.naics_level)
               if lv in ("sector", "subsector", "industry_group")}

    def parent(r):
        if r.naics_level in ("unclassified", "classified_subtotal"):
            return root_id
        if r.naics_level == "sector":
            return classified_id
        if r.naics_level == "subsector":
            return by_code.get(RANGE_SECTORS.get(r.classification_code[:2], r.classification_code[:2]))
        if r.naics_level == "industry_group":
            return by_code.get(r.classification_code[:3])
        return None

    na["derived_parent_id"] = na.apply(parent, axis=1)
    # The data file labels NAICS members as "<metadata name> [<classification code>]".
    na["data_label_expected"] = na.member_name + " [" + na.classification_code + "]"
    counts = na.member_name.value_counts()
    na["label_reused_across_levels"] = na.member_name.map(counts) > 1
    return na.rename(columns={"member_id": "naics_id", "member_name": "naics",
                              "classification_code": "naics_code"})


def parse_band(label):
    m = re.match(r"(\d+) to (\d+) employees", label)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+) (?:plus employees|employees or more)", label)
    if m:
        return int(m.group(1)), None
    return None, None


# ---------------------------------------------------
# 3. Cache (single scan of the raw CSV) + parity
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
            CAST(split_part(COORDINATE, '.', 2) AS INTEGER)    AS size_id,
            CAST(split_part(COORDINATE, '.', 3) AS INTEGER)    AS naics_id
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
    print(f"Cache written: {target} ({target.stat().st_size / 1024**2:.1f} MB) in {time.time() - t0:.1f}s")


def raw_profile_independent():
    """Re-read the raw CSV with Python's csv module (independent of DuckDB)."""
    raw = CSV_FILE.read_bytes()
    try:
        raw.decode("utf-8")
        valid_utf8 = True
    except UnicodeDecodeError:
        valid_utf8 = False

    n_rows, digest, status_counts = 0, 0, {}
    with open(CSV_FILE, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            n_rows += 1
            digest = (digest + row_digest(row)) % (1 << 64)
            status_counts[row[12]] = status_counts.get(row[12], 0) + 1

    return {
        "header": header,
        "rows": n_rows,
        "digest": digest,
        "status_counts": status_counts,
        "newline_count": raw.count(b"\n"),
        "crlf_count": raw.count(b"\r\n"),
        "has_utf8_bom": raw.startswith(b"\xef\xbb\xbf"),
        "valid_utf8": valid_utf8,
        # An empty field written without quotes would appear as ,, or a trailing comma.
        "unquoted_empty_fields": raw.count(b",,") + raw.count(b",\n") + raw.count(b",\r\n"),
        "contains_nbsp": "\u00a0".encode("utf-8") in raw,
        "contains_replacement_char": "\ufffd".encode("utf-8") in raw,
    }


def cache_digest(con):
    cols = ", ".join(f'"{c}"' for c in RAW_COLUMNS)
    digest = 0
    cur = con.execute(f"SELECT {cols} FROM read_parquet('{CACHE_FILE.as_posix()}')")
    while batch := cur.fetchmany(200_000):
        for row in batch:
            digest = (digest + row_digest(row)) % (1 << 64)
    return digest


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():
    start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    banner("STATCAN 33-10-1097-01 — PHASE B VALIDATION")

    # ---------------------------------------------------
    # 1. Raw integrity before processing
    # ---------------------------------------------------
    banner("1. RAW INTEGRITY (BEFORE)")
    check_raw_against_manifest("before")
    gate("csv_identical_to_zip_member", zip_member_matches(CSV_FILE.name, CSV_FILE))
    gate("metadata_identical_to_zip_member", zip_member_matches(META_FILE.name, META_FILE))

    # ---------------------------------------------------
    # 2. Metadata
    # ---------------------------------------------------
    banner("2. METADATA")
    meta = read_metadata(META_FILE)
    members = meta["members"]
    geo = classify_geography(members)
    naics = classify_naics(members)
    size = members[members.dim_id == 2].rename(columns={
        "member_id": "size_id", "member_name": "employment_size"})[["size_id", "employment_size", "metadata_parent_id"]]
    size[["lower_employees", "upper_employees"]] = size.employment_size.apply(lambda s: pd.Series(parse_band(s)))

    try:
        meta["raw_bytes"].decode("utf-8")
        gate("metadata_valid_utf8", True)
    except UnicodeDecodeError:
        gate("metadata_valid_utf8", False)

    notes = meta["notes"]
    gate("metadata_notes_1_to_7_present", sorted(notes, key=int) == [str(i) for i in range(1, 8)], ",".join(sorted(notes, key=int)))
    n1 = notes.get("1", "")
    gate("note1_complete_statistical_location_example",
         "statistical locations" in n1
         and "a retail business with 10 stores and a head office is counted 11 times" in n1
         and "guide" in n1 and any("1105_D16_V4" in u for u in meta["note_links"].get("1", [])),
         "statistical locations; 10 stores + head office = 11; Business Register guide 1105_D16_V4")
    gate("note5_identifies_naics_2022", "2022 version of the North American Industry Classification System (NAICS)" in notes.get("5", ""))
    gate("note7_identifies_sgc_2021", "2021 version of the Standard Geographical Classification (SGC)" in notes.get("7", ""))
    split = [k for k, n in meta["notes_csv_fields"].items() if n > 2]
    gate("no_note_truncated_by_embedded_commas_or_quotes",
         all(notes[k].endswith(".") for k in notes)
         and all(len(notes[k]) > len(meta["notes_csv_second_field"][k].strip()) for k in split),
         f"notes split by csv.reader and reconstructed: {split}")

    member_counts = members.groupby("dim_id").size().to_dict()
    print(f"  {meta['cube']['Cube Title']}")
    print(f"  members per dimension: {member_counts}")
    print(f"  geography levels: {geo.geo_level.value_counts().to_dict()}")
    print(f"  NAICS levels: {naics.naics_level.value_counts().to_dict()}")

    gate("naics_levels_all_recognised", not (naics.naics_level == "unclassified_code_shape").any())
    gate("naics_code_hierarchy_complete",
         naics.loc[naics.naics_level != "table_total", "derived_parent_id"].notna().all()
         and set(naics.loc[naics.naics_level.isin(["sector", "subsector"]), "naics_id"])
         <= set(naics.derived_parent_id.dropna().astype(int)),
         "every subsector/group has its parent code; every sector/subsector has children")
    gate("employment_bands_contiguous", bands_contiguous(size), "1 to 500+ with no gaps or overlaps")
    gate("naics_root_has_unclassified_and_classified_children",
         naics.naics_level.value_counts().reindex(["table_total", "unclassified", "classified_subtotal"]).eq(1).all())

    # ---------------------------------------------------
    # 3. Cache + parity
    # ---------------------------------------------------
    banner("3. CACHE + PARITY")
    con = duckdb.connect()
    lineage = ensure_cache(con, root=ROOT, raw_path=CSV_FILE, cache_path=CACHE_FILE, manifest_path=MANIFEST_FILE,
                           schema_version=CACHE_SCHEMAS["statcan_33101097"]["version"],
                           expected_columns=expected_columns("statcan_33101097", CSV_FILE),
                           build=lambda target: build_cache(con, target))
    gate("cache_lineage_current_or_rebuilt_with_parity", lineage["ok"], f"{lineage['action']} ({lineage.get('reason')})")
    if not lineage["ok"]:
        raise SystemExit(f"Cache lineage failed: {lineage}")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{CACHE_FILE.as_posix()}')")

    raw = raw_profile_independent()
    cache_rows = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    gate("header_matches_expected_columns", raw["header"] == RAW_COLUMNS)
    gate("raw_rows_equal_newlines_minus_header", raw["rows"] == raw["newline_count"] - 1,
         f"{raw['rows']:,} rows")
    gate("cache_rows_equal_independent_parse", cache_rows == raw["rows"], f"{cache_rows:,}")
    gate("cache_content_digest_equals_raw", cache_digest(con) == raw["digest"],
         "order-independent sha256 multiset over all 16 raw fields")
    gate("raw_csv_valid_utf8", raw["valid_utf8"])
    gate("no_unquoted_empty_fields_in_raw", raw["unquoted_empty_fields"] == 0)

    null_checks = " + ".join(f'COUNT(*) FILTER (WHERE "{c}" IS NULL)' for c in RAW_COLUMNS)
    gate("no_sql_null_in_any_raw_column", con.execute(f"SELECT {null_checks} FROM t").fetchone()[0] == 0)

    # ---------------------------------------------------
    # 4. Schema profile + integrity
    # ---------------------------------------------------
    banner("4. SCHEMA PROFILE")
    col_rows = []
    for c in RAW_COLUMNS:
        r = con.execute(f"""
        SELECT COUNT(DISTINCT "{c}"), COUNT(*) FILTER (WHERE "{c}" = ''),
               MIN(LENGTH("{c}")), MAX(LENGTH("{c}")), ANY_VALUE("{c}"),
               COUNT(*) FILTER (WHERE "{c}" <> '' AND TRY_CAST("{c}" AS BIGINT) IS NULL)
        FROM t""").fetchone()
        col_rows.append({"column": c, "cache_type": "VARCHAR (raw string)", "distinct_values": r[0],
                         "empty_strings": r[1], "min_length": r[2], "max_length": r[3],
                         "example_value": r[4], "all_nonempty_values_integer": r[5] == 0})
    col_profile = pd.DataFrame(col_rows)
    write_csv(col_profile, "column_profile.csv")
    print(col_profile[["column", "distinct_values", "empty_strings", "all_nonempty_values_integer"]].to_string(index=False))

    prof = to_py(con.execute("""
    SELECT COUNT(*) AS n_rows, COUNT(DISTINCT REF_DATE) AS n_periods, MIN(REF_DATE) AS ref_date,
           COUNT(DISTINCT geo_id) AS geos_with_rows, COUNT(DISTINCT size_id) AS sizes_with_rows,
           COUNT(DISTINCT naics_id) AS naics_with_rows,
           COUNT(DISTINCT COORDINATE) AS n_coordinates, COUNT(DISTINCT VECTOR) AS n_vectors,
           COUNT(DISTINCT COORDINATE || '|' || VECTOR) AS n_pairs,
           COUNT(DISTINCT UOM || '|' || UOM_ID || '|' || SCALAR_FACTOR || '|' || SCALAR_ID || '|' || DECIMALS) AS n_unit_combos,
           ANY_VALUE(UOM || ' / ' || SCALAR_FACTOR || ' / decimals=' || DECIMALS) AS unit,
           MIN(value_num) AS min_value, MAX(value_num) AS max_value,
           COUNT(*) FILTER (WHERE value_num = 0) AS published_zeros,
           COUNT(*) FILTER (WHERE value_num IS NULL) AS null_values,
           COUNT(*) FILTER (WHERE value_num <> FLOOR(value_num)) AS non_integer_values,
           COUNT(*) FILTER (WHERE value_num = 1) AS values_eq_1,
           COUNT(*) FILTER (WHERE value_num < 10) AS values_lt_10,
           COUNT(*) FILTER (WHERE STATUS <> '') AS status_nonblank,
           COUNT(*) FILTER (WHERE SYMBOL <> '') AS symbol_nonblank,
           COUNT(*) FILTER (WHERE TERMINATED <> '') AS terminated_nonblank
    FROM t""").df().iloc[0].to_dict())
    print(f"  {prof}")

    gate("single_reference_period", prof["n_periods"] == 1, prof["ref_date"])
    gate("single_unit_scalar_decimal", prof["n_unit_combos"] == 1, prof["unit"])
    gate("one_vector_per_coordinate_no_duplicate_cells",
         prof["n_coordinates"] == prof["n_vectors"] == prof["n_pairs"] == prof["n_rows"])
    gate("all_values_positive_integers",
         prof["null_values"] == 0 and prof["published_zeros"] == 0 and prof["non_integer_values"] == 0
         and prof["min_value"] >= 1)
    gate("status_symbol_terminated_all_blank",
         prof["status_nonblank"] == 0 and prof["symbol_nonblank"] == 0 and prof["terminated_nonblank"] == 0)

    con.register("geo_m", geo)
    con.register("naics_m", naics)
    con.register("size_m", size)

    orphans = con.execute("""
    SELECT COUNT(*) FROM (SELECT DISTINCT geo_id, size_id, naics_id FROM t)
    WHERE geo_id NOT IN (SELECT geo_id FROM geo_m) OR size_id NOT IN (SELECT size_id FROM size_m)
       OR naics_id NOT IN (SELECT naics_id FROM naics_m)""").fetchone()[0]
    gate("no_orphan_coordinates", orphans == 0)

    label_mismatch = con.execute(f"""
    SELECT
      (SELECT COUNT(*) FROM (SELECT DISTINCT geo_id, GEO FROM t) d JOIN geo_m USING (geo_id) WHERE d.GEO <> geo_m.geo)
    + (SELECT COUNT(*) FROM (SELECT DISTINCT size_id, "Employment size" AS l FROM t) d JOIN size_m USING (size_id) WHERE d.l <> size_m.employment_size)
    + (SELECT COUNT(*) FROM (SELECT DISTINCT naics_id, "{NAICS_COL}" AS l FROM t) d JOIN naics_m USING (naics_id) WHERE d.l <> naics_m.data_label_expected)
    """).fetchone()[0]
    gate("data_labels_match_metadata", label_mismatch == 0,
         "GEO and size labels = metadata name; NAICS label = metadata name + ' [code]'")

    dguid_mismatch = con.execute("""
    SELECT COUNT(*) FROM (SELECT DISTINCT geo_id, DGUID FROM t) d JOIN geo_m USING (geo_id)
    WHERE d.DGUID IS DISTINCT FROM geo_m.expected_dguid""").fetchone()[0]
    gate("dguid_equals_2021_vintage_prefix_plus_code", dguid_mismatch == 0)

    listed = geo[geo.geo_level != "grouping_root"]
    top_cells = con.execute("SELECT COUNT(DISTINCT geo_id) FROM t WHERE size_id = 1 AND naics_id = 1").fetchone()[0]
    gate("every_geography_with_rows_has_top_cell", top_cells == prof["geos_with_rows"],
         f"{top_cells:,} of {prof['geos_with_rows']:,}")
    gate("grouping_root_has_no_rows",
         con.execute(f"SELECT COUNT(*) FROM t WHERE geo_id IN (SELECT geo_id FROM geo_m WHERE geo_level = 'grouping_root')").fetchone()[0] == 0)

    # ---------------------------------------------------
    # 5. Reconciliation (additivity) — mutually exclusive children only
    # ---------------------------------------------------
    banner("5. RECONCILIATION")

    def reconcile(test, parent_desc, children_desc, child_sql, parent_where):
        r = con.execute(f"""
        WITH ch AS ({child_sql}),
             pa AS (SELECT geo_id, size_id, naics_id, value_num AS parent_value FROM t WHERE {parent_where})
        SELECT COUNT(*) AS parent_cells_compared,
               COUNT(*) FILTER (WHERE parent_value = child_sum) AS exact_matches,
               COUNT(*) FILTER (WHERE parent_value > child_sum) AS parent_greater,
               COUNT(*) FILTER (WHERE parent_value < child_sum) AS parent_smaller,
               COUNT(*) FILTER (WHERE parent_value IS NULL) AS parent_omitted_children_published,
               COUNT(*) FILTER (WHERE child_sum IS NULL) AS parent_published_children_omitted,
               COALESCE(MAX(ABS(COALESCE(parent_value, 0) - COALESCE(child_sum, 0))), 0) AS max_abs_difference,
               COALESCE(SUM(parent_value), 0) AS sum_parent_values,
               COALESCE(SUM(child_sum), 0) AS sum_child_values
        FROM pa FULL OUTER JOIN ch USING (geo_id, size_id, naics_id)
        """).df().iloc[0].to_dict()
        r = to_py(r)
        r["pct_exact"] = round(100.0 * r["exact_matches"] / r["parent_cells_compared"], 4) if r["parent_cells_compared"] else None
        return {"test": test, "parent": parent_desc, "children": children_desc,
                "children_mutually_exclusive_basis": "", "status": "tested", **r}

    naics_child = """SELECT geo_id, size_id, n.derived_parent_id AS naics_id, SUM(value_num) AS child_sum
                     FROM t JOIN naics_m n USING (naics_id) WHERE n.naics_level = '{lvl}' GROUP BY ALL"""
    tests = [
        reconcile("size_total_vs_bands", "Total, with employees", "8 employment-size bands",
                  "SELECT geo_id, 1 AS size_id, naics_id, SUM(value_num) AS child_sum FROM t WHERE size_id > 1 GROUP BY ALL",
                  "size_id = 1"),
        reconcile("naics_total_vs_unclassified_plus_classified", "Total, all industries [1]",
                  "Unclassified [2] + Sub-total, classified [3]",
                  "SELECT geo_id, size_id, n.derived_parent_id AS naics_id, SUM(value_num) AS child_sum FROM t JOIN naics_m n USING (naics_id) WHERE n.naics_level IN ('unclassified', 'classified_subtotal') GROUP BY ALL",
                  "naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'table_total')"),
        reconcile("naics_classified_vs_sectors", "Sub-total, classified [3]", "20 NAICS sectors",
                  naics_child.format(lvl="sector"),
                  "naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'classified_subtotal')"),
        reconcile("naics_sector_vs_subsectors", "each NAICS sector", "its 3-digit subsectors (by code)",
                  naics_child.format(lvl="subsector"),
                  "naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'sector')"),
        reconcile("naics_subsector_vs_industry_groups", "each 3-digit subsector", "its 4-digit industry groups (by code)",
                  naics_child.format(lvl="industry_group"),
                  "naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'subsector')"),
        reconcile("geography_cma_vs_its_csds", "each CMA", "its CSDs (metadata parent)",
                  "SELECT CAST(g.metadata_parent_id AS INTEGER) AS geo_id, size_id, naics_id, SUM(value_num) AS child_sum FROM t JOIN geo_m g USING (geo_id) WHERE g.geo_level = 'CSD_in_CMA' GROUP BY ALL",
                  "geo_id IN (SELECT geo_id FROM geo_m WHERE geo_level = 'CMA')"),
    ]
    basis = {
        "size_total_vs_bands": "Bands 1-4 ... 500+ are contiguous and non-overlapping; each location has one employment figure.",
        "naics_total_vs_unclassified_plus_classified": "Metadata children of [1]; a unit either has a NAICS code or not (Note 6).",
        "naics_classified_vs_sectors": "NAICS 2022 sectors partition classified units (Note 5).",
        "naics_sector_vs_subsectors": "NAICS code prefix hierarchy; metadata parent IDs are flat below [3].",
        "naics_subsector_vs_industry_groups": "NAICS code prefix hierarchy; metadata parent IDs are flat below [3].",
        "geography_cma_vs_its_csds": "SGC 2021 CSDs partition a CMA; a location resides in one smallest geographic area.",
    }
    for tst in tests:
        tst["children_mutually_exclusive_basis"] = basis[tst["test"]]

    not_testable = [
        {"test": "geography_province_vs_components", "parent": "province / territory", "children": "CMAs and CSDs",
         "status": "not_testable",
         "children_mutually_exclusive_basis": "No province, territory or Canada members exist in this table."},
        {"test": "geography_other_csd_group_vs_csds", "parent": GROUPING_ROOT_NAME, "children": "CSDs outside CMAs",
         "status": "not_testable",
         "children_mutually_exclusive_basis": "Grouping member has no data rows; it is not a geography with a total."},
    ]
    recon = pd.DataFrame(tests + not_testable)
    recon = recon[[c for c in recon.columns if c != "children_mutually_exclusive_basis"]
                  + ["children_mutually_exclusive_basis"]]
    write_csv(recon, "reconciliation_summary.csv")
    print(recon[["test", "parent_cells_compared", "exact_matches", "parent_greater", "parent_smaller",
                 "parent_omitted_children_published", "parent_published_children_omitted", "max_abs_difference"]]
          .to_string(index=False))
    for tst in tests:
        gate(f"reconciles_exactly_{tst['test']}",
             tst["exact_matches"] == tst["parent_cells_compared"] > 0
             and tst["parent_omitted_children_published"] == 0 and tst["parent_published_children_omitted"] == 0)
    all_reconcile = all(gates[f"reconciles_exactly_{tst['test']}"] for tst in tests)

    # ---------------------------------------------------
    # 6. Value states over the in-scope grid
    # ---------------------------------------------------
    banner("6. VALUE STATES")
    n_listed_geos = len(listed)
    grid_cells = n_listed_geos * len(size) * len(naics)
    published = prof["n_rows"]
    omitted = grid_cells - published
    legend = meta["legend"]
    state_rows = [
        {"state": "published_positive_count", "status_code": "(blank)", "cells": published,
         "definition": BLANK_STATUS_DEFINITION, "definition_source": "StatCan Standard table symbols page (blank not in local legend)"},
        {"state": "published_zero", "status_code": "(blank)", "cells": prof["published_zeros"],
         "definition": "value 0 printed in the file", "definition_source": "data"},
        {"state": "confidentiality_suppressed", "status_code": "x", "cells": 0,
         "definition": legend.get("x", ""), "definition_source": "local metadata Symbol Legend"},
        {"state": "not_available_for_period", "status_code": "..", "cells": 0,
         "definition": legend.get("..", ""), "definition_source": "local metadata Symbol Legend"},
        {"state": "use_with_caution", "status_code": "E", "cells": 0,
         "definition": legend.get("E", ""), "definition_source": "local metadata Symbol Legend"},
        {"state": "too_unreliable", "status_code": "F", "cells": 0,
         "definition": legend.get("F", ""), "definition_source": "local metadata Symbol Legend"},
        {"state": "omitted_zero_by_reconciliation", "status_code": "(no row)",
         "cells": omitted if all_reconcile else None,
         "definition": "in-scope cell with no row; every published parent equals the sum of its published children, so omitted cells sum to 0",
         "definition_source": "data (reconciliation_summary.csv)"},
        {"state": "unexplained_missing", "status_code": "(no row)", "cells": 0 if all_reconcile else omitted,
         "definition": "in-scope cell with no row that reconciliation cannot account for",
         "definition_source": "data"},
        {"state": "out_of_scope_not_in_table", "status_code": "(not listed)", "cells": None,
         "definition": "Canada, provinces, territories, and CSDs outside CMAs with fewer than 10 active businesses (Note 2). Not zero; unknown.",
         "definition_source": "local metadata Note 2 + dimension members"},
    ]
    for r in state_rows:
        r["share_of_in_scope_grid_pct"] = round(100.0 * r["cells"] / grid_cells, 3) if r["cells"] is not None else None
    states = pd.DataFrame(state_rows)
    write_csv(states, "status_and_value_states.csv")
    status_counts_cache = dict(con.execute("SELECT STATUS, COUNT(*) FROM t GROUP BY 1").fetchall())
    gate("status_counts_match_independent_parse", status_counts_cache == raw["status_counts"],
         f"{ {('(blank)' if k == '' else k): v for k, v in status_counts_cache.items()} }")
    print(states[["state", "status_code", "cells", "share_of_in_scope_grid_pct"]].to_string(index=False))
    print(f"  in-scope grid: {n_listed_geos:,} geographies x {len(size)} sizes x {len(naics)} NAICS = {grid_cells:,} cells")

    # ---------------------------------------------------
    # 7. Availability / density matrix: geography level x NAICS level x size
    #    A "block" = one geography x one NAICS level x one size band.
    # ---------------------------------------------------
    banner("7. AVAILABILITY MATRIX")
    matrix = con.execute("""
    WITH lv AS (SELECT naics_level, COUNT(*) AS members_in_level FROM naics_m GROUP BY 1),
         gl AS (SELECT geo_level, COUNT(*) AS geos_in_level FROM geo_m WHERE geo_level <> 'grouping_root' GROUP BY 1),
         blk AS (
            SELECT g.geo_level, n.naics_level, t.size_id, t.geo_id, COUNT(*) AS published_in_block
            FROM t JOIN geo_m g USING (geo_id) JOIN naics_m n USING (naics_id) GROUP BY ALL),
         cells AS (
            SELECT g.geo_level, n.naics_level, t.size_id,
                   COUNT(*) AS published_cells,
                   COUNT(*) FILTER (WHERE value_num = 1) AS published_eq_1,
                   COUNT(*) FILTER (WHERE value_num >= 10) AS published_ge_10,
                   MEDIAN(value_num) AS median_published_value,
                   SUM(value_num) AS sum_published_values
            FROM t JOIN geo_m g USING (geo_id) JOIN naics_m n USING (naics_id) GROUP BY ALL)
    SELECT gl.geo_level, lv.naics_level, s.size_id, s.employment_size,
           gl.geos_in_level * lv.members_in_level AS possible_cells,
           COALESCE(c.published_cells, 0) AS published_cells,
           gl.geos_in_level * lv.members_in_level - COALESCE(c.published_cells, 0) AS zero_by_reconciliation_cells,
           ROUND(100.0 * COALESCE(c.published_cells, 0) / (gl.geos_in_level * lv.members_in_level), 2) AS pct_cells_published,
           ROUND(100.0 * c.published_eq_1 / NULLIF(c.published_cells, 0), 2) AS pct_published_eq_1,
           ROUND(100.0 * c.published_ge_10 / NULLIF(c.published_cells, 0), 2) AS pct_published_ge_10,
           c.median_published_value,
           gl.geos_in_level AS possible_blocks,
           (SELECT COUNT(*) FROM blk b WHERE b.geo_level = gl.geo_level AND b.naics_level = lv.naics_level
               AND b.size_id = s.size_id AND b.published_in_block = lv.members_in_level) AS blocks_fully_published,
           (SELECT COUNT(*) FROM blk b WHERE b.geo_level = gl.geo_level AND b.naics_level = lv.naics_level
               AND b.size_id = s.size_id AND b.published_in_block < lv.members_in_level) AS blocks_partially_published,
           gl.geos_in_level - (SELECT COUNT(*) FROM blk b WHERE b.geo_level = gl.geo_level AND b.naics_level = lv.naics_level
               AND b.size_id = s.size_id) AS blocks_all_zero
    FROM gl CROSS JOIN lv CROSS JOIN size_m s
    LEFT JOIN cells c ON c.geo_level = gl.geo_level AND c.naics_level = lv.naics_level AND c.size_id = s.size_id
    ORDER BY gl.geo_level, CASE lv.naics_level WHEN 'table_total' THEN 1 WHEN 'unclassified' THEN 2
             WHEN 'classified_subtotal' THEN 3 WHEN 'sector' THEN 4 WHEN 'subsector' THEN 5 ELSE 6 END, s.size_id
    """).df()
    write_csv(matrix, "availability_matrix.csv")
    gate("availability_matrix_accounts_for_every_published_row", int(matrix.published_cells.sum()) == published)
    gate("availability_matrix_covers_in_scope_grid", int(matrix.possible_cells.sum()) == grid_cells)
    show = matrix[(matrix.size_id == 1) | (matrix.naics_level == "sector")]
    print(show[["geo_level", "naics_level", "employment_size", "pct_cells_published", "pct_published_eq_1",
                "pct_published_ge_10"]].head(24).to_string(index=False))

    # ---------------------------------------------------
    # 8. Per-member profiles: geography, NAICS, employment size
    # ---------------------------------------------------
    banner("8. MEMBER PROFILES")
    geo_stats = con.execute("""
    SELECT geo_id,
           COUNT(*) AS published_cells,
           MAX(value_num) FILTER (WHERE size_id = 1 AND naics_id = 1) AS locations_total,
           MAX(value_num) FILTER (WHERE size_id = 1 AND naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'unclassified')) AS locations_unclassified,
           COUNT(*) FILTER (WHERE size_id = 1 AND naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'sector')) AS sectors_with_locations,
           COUNT(*) FILTER (WHERE size_id = 1 AND naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'subsector')) AS subsectors_with_locations,
           COUNT(*) FILTER (WHERE size_id = 1 AND naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'industry_group')) AS industry_groups_with_locations,
           COUNT(*) FILTER (WHERE size_id > 1 AND naics_id = 1) AS size_bands_with_locations
    FROM t GROUP BY 1""").df()
    geo_profile = geo.merge(geo_stats, on="geo_id", how="left")
    geo_profile["has_rows"] = geo_profile.published_cells.notna()
    geo_profile["pct_unclassified"] = (100 * geo_profile.locations_unclassified / geo_profile.locations_total).round(2)
    geo_profile = geo_profile[[
        "geo_id", "geo_code", "geo", "geo_level", "expected_dguid", "province_code", "parent_cma_code", "parent_cma",
        "has_rows", "published_cells", "locations_total", "locations_unclassified", "pct_unclassified",
        "sectors_with_locations", "subsectors_with_locations", "industry_groups_with_locations",
        "size_bands_with_locations", "label_shared_by_members", "label_equals_parent_cma_label"]]
    write_csv(geo_profile.rename(columns={"expected_dguid": "dguid"}), "geography_profile.csv")

    csd_out = geo_profile[geo_profile.geo_level == "CSD_outside_CMA"]
    csd_in = geo_profile[geo_profile.geo_level == "CSD_in_CMA"]
    gate("csds_outside_cmas_all_have_at_least_10_locations", (csd_out.locations_total >= 10).all(),
         f"min {int(csd_out.locations_total.min())} (consistent with Note 2)")
    no_row_levels = set(geo_profile.loc[(geo_profile.geo_level != "grouping_root") & ~geo_profile.has_rows, "geo_level"])
    gate("geographies_without_rows_are_all_cma_components", no_row_levels <= {"CSD_in_CMA"},
         f"{int(((geo_profile.geo_level != 'grouping_root') & ~geo_profile.has_rows).sum())} listed geographies without rows")
    gate("cma_csds_without_rows_reconcile_to_zero",
         gates["reconciles_exactly_geography_cma_vs_its_csds"], f"{int((~csd_in.has_rows).sum())} CSDs with no rows")

    cma_ids = "(SELECT geo_id FROM geo_m WHERE geo_level = 'CMA')"
    naics_stats = con.execute(f"""
    SELECT naics_id, COUNT(*) AS published_cells,
           SUM(value_num) FILTER (WHERE size_id = 1 AND geo_id IN {cma_ids}) AS locations_all_41_cmas,
           COUNT(*) FILTER (WHERE size_id = 1 AND geo_id IN {cma_ids}) AS cmas_with_locations
    FROM t GROUP BY 1""").df()
    naics_profile = naics.merge(naics_stats, on="naics_id", how="left")
    naics_profile["derived_parent_code"] = naics_profile.derived_parent_id.map(dict(zip(naics.naics_id, naics.naics_code)))
    naics_profile = naics_profile[[
        "naics_id", "naics_code", "naics", "naics_level", "metadata_parent_id", "derived_parent_id",
        "derived_parent_code", "member_notes", "label_reused_across_levels", "published_cells",
        "locations_all_41_cmas", "cmas_with_locations"]]
    write_csv(naics_profile, "naics_profile.csv")

    size_stats = con.execute(f"""
    SELECT size_id, COUNT(*) AS published_cells,
           SUM(value_num) FILTER (WHERE naics_id = 1 AND geo_id IN {cma_ids}) AS locations_all_41_cmas,
           SUM(value_num) FILTER (WHERE naics_id IN (SELECT naics_id FROM naics_m WHERE naics_level = 'unclassified')
                                  AND geo_id IN {cma_ids}) AS unclassified_all_41_cmas
    FROM t GROUP BY 1""").df()
    size_profile = size.merge(size_stats, on="size_id", how="left")
    size_profile["pct_unclassified_41_cmas"] = (100 * size_profile.unclassified_all_41_cmas
                                                / size_profile.locations_all_41_cmas).round(2)

    # ---------------------------------------------------
    # 9. Compatibility with 33-10-0722-01 (diagnostic)
    # ---------------------------------------------------
    banner("9. COMPATIBILITY WITH 33-10-0722-01")
    ok_0722, why_0722, lin_0722 = check_lineage(
        con, root=ROOT, raw_path=RAW_0722, cache_path=CACHE_0722, manifest_path=MANIFEST_FILE,
        schema_version=CACHE_SCHEMAS["statcan_33100722"]["version"],
        expected_columns=expected_columns("statcan_33100722", RAW_0722))
    gate("phase_a_cache_lineage_current", ok_0722, why_0722)
    if not ok_0722:
        print("Phase A cache is not current. Run 03_availability_statcan_33100722.py, then rerun this script.")
        sys.exit(1)
    con.execute(f"CREATE VIEW a AS SELECT * FROM read_parquet('{CACHE_0722.as_posix()}')")
    n_0722 = con.execute("SELECT COUNT(*) FROM a").fetchone()[0]
    gate("cache_0722_matches_phase_a_row_count", n_0722 == EXPECTED_0722_ROWS, f"{n_0722:,}")

    # Size bands: do 1097 bands nest numerically into 0722 bands? (diagnostic, not a crosswalk)
    bands_0722 = con.execute('SELECT DISTINCT "Employment size" FROM a').df()["Employment size"].tolist()
    parsed_0722 = [(b, *parse_band(b)) for b in bands_0722 if parse_band(b)[0] is not None]

    def containing_0722_band(lo, hi):
        if pd.isna(lo):
            return None
        for label, blo, bhi in parsed_0722:
            if lo >= blo and (bhi is None or (not pd.isna(hi) and hi <= bhi)):
                return label
        return "NONE"

    size_profile["contained_in_0722_band_numeric"] = [containing_0722_band(lo, hi) for lo, hi
                                                      in zip(size_profile.lower_employees, size_profile.upper_employees)]
    write_csv(size_profile, "employment_size_profile.csv")
    print(size_profile[["employment_size", "locations_all_41_cmas", "pct_unclassified_41_cmas",
                        "contained_in_0722_band_numeric"]].to_string(index=False))
    gate("every_1097_band_nests_in_one_0722_band_numerically",
         (size_profile.contained_in_0722_band_numeric.dropna() != "NONE").all())

    sector_map = pd.DataFrame([(k, c) for k, v in SECTOR_MAP_0722.items() for c in v],
                              columns=["sector_0722", "naics_code"])
    con.register("sector_map", sector_map)
    con.execute("""
    CREATE TEMP TABLE b_codes AS
    SELECT t.*, n.naics_code, n.naics_level FROM t JOIN naics_m n USING (naics_id)""")

    cma = con.execute("""
    WITH a_cma AS (
        SELECT RIGHT(DGUID, 3) AS cma_code, ANY_VALUE(DGUID) AS dguid_0722, ANY_VALUE(GEO) AS geo_0722,
               MAX(value_num) FILTER (WHERE REF_DATE = '2025-07' AND industry_id = 1 AND size_id = 1 AND measure_id = 1) AS active_enterprises_0722_2025_07,
               MAX(value_num) FILTER (WHERE REF_DATE = '2025-12' AND industry_id = 1 AND size_id = 1 AND measure_id = 1) AS active_enterprises_0722_2025_12,
               COUNT(DISTINCT industry_id) FILTER (WHERE REF_DATE = '2025-12' AND measure_id = 1 AND size_id = 1
                   AND value_num IS NOT NULL AND industry_id BETWEEN 2 AND 19) AS sectors_with_values_0722_2025_12,
               COUNT(DISTINCT size_id) FILTER (WHERE REF_DATE = '2025-12' AND measure_id = 1 AND industry_id = 1
                   AND value_num IS NOT NULL AND size_id > 1) AS size_bands_with_values_0722_2025_12
        FROM a WHERE DGUID LIKE '2016S0503%' GROUP BY 1),
    b_cma AS (
        SELECT RIGHT(DGUID, 3) AS cma_code, ANY_VALUE(DGUID) AS dguid_1097, ANY_VALUE(GEO) AS geo_1097,
               SUM(value_num) FILTER (WHERE size_id = 1 AND naics_level = 'table_total') AS locations_all_industries_1097,
               SUM(value_num) FILTER (WHERE size_id = 1 AND naics_level = 'unclassified') AS locations_unclassified_1097,
               SUM(value_num) FILTER (WHERE size_id = 1 AND naics_code IN (SELECT naics_code FROM sector_map)) AS locations_0722_sector_list_1097,
               SUM(value_num) FILTER (WHERE size_id = 1 AND naics_code IN ('113', '114', '115')) AS locations_naics_113_115_1097,
               COUNT(*) FILTER (WHERE size_id = 1 AND naics_level = 'sector') AS sectors_with_locations_1097,
               COUNT(*) FILTER (WHERE size_id = 1 AND naics_level = 'industry_group') AS industry_groups_with_locations_1097,
               COUNT(*) FILTER (WHERE size_id > 1 AND naics_level = 'table_total') AS size_bands_with_locations_1097
        FROM b_codes WHERE DGUID LIKE '2021S0503%' GROUP BY 1)
    SELECT * FROM b_cma FULL OUTER JOIN a_cma USING (cma_code) ORDER BY cma_code
    """).df()
    cma["in_0722"] = cma.dguid_0722.notna()
    cma["in_1097"] = cma.dguid_1097.notna()
    # Name comparison only (codes are the join key): first segment of the 0722 label, NBSP-trimmed.
    name_0722 = cma.geo_0722.str.replace("\u00a0", " ").str.strip().str.split(",").str[0]
    cma["label_differs"] = cma.in_0722 & cma.in_1097 & (name_0722 != cma.geo_1097)
    cma["pct_unclassified_1097"] = (100 * cma.locations_unclassified_1097 / cma.locations_all_industries_1097).round(2)
    cma["pct_naics_113_115_in_sector_list"] = (100 * cma.locations_naics_113_115_1097 / cma.locations_0722_sector_list_1097).round(3)
    for period in ("2025_07", "2025_12"):
        den = cma[f"active_enterprises_0722_{period}"]
        cma[f"ratio_all_industries_{period}"] = (cma.locations_all_industries_1097 / den).round(4)
        cma[f"ratio_sector_list_excl_unclassified_{period}"] = (cma.locations_0722_sector_list_1097 / den).round(4)
        cma[f"ratio_sector_list_incl_unclassified_{period}"] = (
            (cma.locations_0722_sector_list_1097 + cma.locations_unclassified_1097) / den).round(4)
    write_csv(cma, "cma_comparison_0722.csv")

    shared = cma[cma.in_0722 & cma.in_1097]
    gate("every_0722_cma_code_present_in_1097", int(cma.in_0722.sum()) == len(shared), f"{len(shared)} shared")
    show_cols = ["cma_code", "geo_1097", "ratio_sector_list_excl_unclassified_2025_12",
                 "ratio_sector_list_incl_unclassified_2025_12", "ratio_all_industries_2025_12", "pct_unclassified_1097"]
    print(shared[show_cols].sort_values("ratio_sector_list_excl_unclassified_2025_12").to_string(index=False))

    sector_ratio = con.execute("""
    WITH a_s AS (
        SELECT RIGHT(DGUID, 3) AS cma_code, regexp_extract(Industry, '\\[([^\\]]+)\\]$', 1) AS sector_0722,
               value_num AS active_enterprises_0722
        FROM a WHERE DGUID LIKE '2016S0503%' AND REF_DATE = '2025-12' AND size_id = 1 AND measure_id = 1
          AND industry_id BETWEEN 2 AND 19 AND value_num IS NOT NULL),
    b_s AS (
        SELECT RIGHT(DGUID, 3) AS cma_code, m.sector_0722, SUM(value_num) AS locations_1097
        FROM b_codes JOIN sector_map m USING (naics_code)
        WHERE DGUID LIKE '2021S0503%' AND size_id = 1 GROUP BY ALL)
    SELECT sector_0722, COUNT(*) AS cmas_compared,
           ROUND(MIN(locations_1097 / active_enterprises_0722), 3) AS min_ratio,
           ROUND(MEDIAN(locations_1097 / active_enterprises_0722), 3) AS median_ratio,
           ROUND(MAX(locations_1097 / active_enterprises_0722), 3) AS max_ratio
    FROM a_s JOIN b_s USING (cma_code, sector_0722) GROUP BY 1 ORDER BY median_ratio
    """).df()
    sector_ratio["naics_codes_used_1097"] = sector_ratio.sector_0722.map(lambda s: ";".join(SECTOR_MAP_0722[s]))
    write_csv(sector_ratio, "sector_ratio_diagnostic_0722.csv")
    print(sector_ratio.to_string(index=False))

    # ---------------------------------------------------
    # 10. Summary
    # ---------------------------------------------------
    def rng(col):
        s = shared[col]
        return {"min": round(float(s.min()), 3), "median": round(float(s.median()), 3), "max": round(float(s.max()), 3)}

    summary = {
        "source": "Statistics Canada 33-10-1097-01",
        "metadata": {
            "cube_title": meta["cube"]["Cube Title"],
            "product_id": meta["cube"]["Product Id"],
            "table_page_url": meta["cube"]["URL"],
            "frequency": meta["cube"]["Frequency"],
            "start_reference_period": meta["cube"]["Start Reference Period"],
            "end_reference_period": meta["cube"]["End Reference Period"],
            "cube_notes": meta["cube"]["Cube Notes"],
            "survey": meta["survey"],
            "dimensions": {str(k): v for k, v in meta["dims"].items()},
            "members_per_dimension": {str(k): int(v) for k, v in member_counts.items()},
            "notes": meta["notes"],
            "notes_raw": meta["notes_raw"],
            "note_links": meta["note_links"],
            "notes_split_by_csv_reader": {k: n for k, n in meta["notes_csv_fields"].items() if n > 2},
            "legend_symbols_available": list(meta["legend"].keys()),
        },
        "raw": {
            "csv_bytes": CSV_FILE.stat().st_size,
            "metadata_bytes": META_FILE.stat().st_size,
            "csv_sha256": sha256_file(CSV_FILE),
            "has_utf8_bom": raw["has_utf8_bom"],
            "line_endings": "LF" if raw["crlf_count"] == 0 else "CRLF",
            "contains_nbsp": raw["contains_nbsp"],
            "contains_replacement_char": raw["contains_replacement_char"],
            "blank_encoding": 'every field is quoted; empty fields are written as "" (no unquoted empties)',
            "status_counts_raw_parse": {("(blank)" if k == "" else k): v for k, v in raw["status_counts"].items()},
        },
        "cache_lineage": {k: v for k, v in lineage.items() if k != "lineage"},
        "cache_lineage_record": lineage.get("lineage"),
        "phase_a_cache_lineage": {"current": ok_0722, "reason": why_0722,
                                  "source_sha256": (lin_0722 or {}).get("source_sha256"),
                                  "cache_sha256": (lin_0722 or {}).get("cache_sha256")},
        "profile": prof,
        "geography": {
            "levels": geo.geo_level.value_counts().to_dict(),
            "geographies_with_rows": prof["geos_with_rows"],
            "cma_csds_without_rows": int((~csd_in.has_rows).sum()),
            "csd_outside_cma_min_locations": int(csd_out.locations_total.min()),
            "cma_csds_with_fewer_than_10_locations": int((csd_in.locations_total < 10).sum()),
            "labels_shared_by_more_than_one_member": int((geo.geo.value_counts() > 1).sum()),
            "members_with_shared_labels": int((geo.label_shared_by_members > 1).sum()),
            "csds_labelled_same_as_parent_cma": int(geo.label_equals_parent_cma_label.sum()),
            "cmas_spanning_two_provinces": geo.loc[(geo.geo_level == "CMA") & geo.province_code.fillna("").str.contains(";"), "geo"].tolist(),
            "csds_by_province_code": {k: {lv: int(n) for lv, n in v.items() if n}
                                      for k, v in pd.crosstab(geo.province_code, geo.geo_level)
                                      .drop(columns=[c for c in ("CMA",) if c in geo.geo_level.unique()])
                                      .loc[lambda d: d.index.str.len() == 2].to_dict(orient="index").items()},
        },
        "naics": {
            "levels": naics.naics_level.value_counts().to_dict(),
            "labels_reused_across_levels": int(naics.loc[naics.label_reused_across_levels, "naics"].nunique()),
            "members_with_reused_labels": int(naics.label_reused_across_levels.sum()),
            "data_label_format": "metadata name + ' [classification code]'",
            "metadata_parent_of_all_sectors_subsectors_groups": "Sub-total, classified [3]",
        },
        "in_scope_grid_cells": grid_cells,
        "value_states": {r["state"]: r["cells"] for r in state_rows},
        "reconciliation": {tst["test"]: {"compared": tst["parent_cells_compared"], "exact": tst["exact_matches"]} for tst in tests},
        "compatibility_0722": {
            "cma_codes_shared": len(shared),
            "cma_codes_only_in_1097": cma.loc[~cma.in_0722, "geo_1097"].tolist(),
            "cma_labels_differing": cma.loc[cma.label_differs, ["cma_code", "geo_0722", "geo_1097"]].to_dict(orient="records"),
            "ratio_sector_list_excl_unclassified_2025_12": rng("ratio_sector_list_excl_unclassified_2025_12"),
            "ratio_sector_list_incl_unclassified_2025_12": rng("ratio_sector_list_incl_unclassified_2025_12"),
            "ratio_all_industries_2025_12": rng("ratio_all_industries_2025_12"),
            "ratio_sector_list_excl_unclassified_2025_07": rng("ratio_sector_list_excl_unclassified_2025_07"),
            "ratio_sector_list_incl_unclassified_2025_07": rng("ratio_sector_list_incl_unclassified_2025_07"),
            "ratio_all_industries_2025_07": rng("ratio_all_industries_2025_07"),
            "max_abs_change_in_sector_list_ratio_2025_07_vs_2025_12": round(float(
                (shared.ratio_sector_list_excl_unclassified_2025_07 - shared.ratio_sector_list_excl_unclassified_2025_12).abs().max()), 4),
            "pct_unclassified_1097_by_cma": {"min": float(cma.pct_unclassified_1097.min()),
                                             "max": float(cma.pct_unclassified_1097.max())},
            "max_pct_naics_113_115_in_sector_list": float(shared.pct_naics_113_115_in_sector_list.max()),
            "sector_median_ratio_range_excl_11": {
                "min": float(sector_ratio.loc[sector_ratio.sector_0722 != "11", "median_ratio"].min()),
                "max": float(sector_ratio.loc[sector_ratio.sector_0722 != "11", "median_ratio"].max())},
        },
        "gates": gates,
    }

    # ---------------------------------------------------
    # 11. Raw integrity after processing + exit status
    # ---------------------------------------------------
    banner("11. RAW INTEGRITY (AFTER)")
    check_raw_against_manifest("after")

    summary["gates"] = gates
    summary["all_gates_pass"] = all(gates.values())
    summary["elapsed_seconds"] = round(time.time() - start, 1)
    with open(OUTPUT_DIR / "phase_b_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str, ensure_ascii=False)

    banner("PHASE B COMPLETE")
    failed = [k for k, v in gates.items() if not v]
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass")
    for k in failed:
        print(f"  FAILED: {k}")
    print(f"Outputs: {OUTPUT_DIR}")
    print(f"Elapsed: {summary['elapsed_seconds']}s")
    con.close()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
