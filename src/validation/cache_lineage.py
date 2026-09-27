"""
Raw-to-cache lineage for the disposable Parquet caches under data/cache/.

A cache is reused only if its lineage file proves it was built from the exact raw
file recorded in data/source_manifest.csv, with the current schema version, and the
cache file itself is byte-identical to the one that passed parity at build time.
Otherwise it is rebuilt: the new cache is installed only after an independent parse
of the raw CSV (Python csv module) and the cached raw fields give identical row
counts and identical order-independent content digests.

Lineage file: <cache>.lineage.json, next to the cache (gitignored, disposable).
"""

from datetime import datetime
import csv
import hashlib
import json

import pandas as pd

# Cache schemas shared by the scripts that build and the scripts that consume each cache:
# every raw CSV column as VARCHAR, followed by these derived columns.
CACHE_SCHEMAS = {
    "statcan_33100722": {"version": "statcan_33100722/v1",
                         "derived": ["value_num", "geo_id", "industry_id", "size_id", "measure_id", "ref_year"]},
    "statcan_33101097": {"version": "statcan_33101097/v1",
                         "derived": ["value_num", "geo_id", "size_id", "naics_id"]},
}


def raw_header(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def expected_columns(key, csv_path):
    return raw_header(csv_path) + CACHE_SCHEMAS[key]["derived"]


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


def raw_csv_digest(csv_path):
    """Independent parse of the raw CSV: header, row count and content digest."""
    n, digest = 0, 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            n += 1
            digest = (digest + row_digest(row)) % (1 << 64)
    return header, n, f"{digest:016x}"


def parquet_raw_digest(con, parquet_path, columns):
    """Digest of the cached copies of the raw columns (same function as raw_csv_digest)."""
    cols = ", ".join(f'"{c}"' for c in columns)
    n, digest = 0, 0
    cur = con.execute(f"SELECT {cols} FROM read_parquet('{parquet_path.as_posix()}')")
    while batch := cur.fetchmany(200_000):
        for row in batch:
            n += 1
            digest = (digest + row_digest(row)) % (1 << 64)
    return n, f"{digest:016x}"


def parquet_columns(con, parquet_path):
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet_path.as_posix()}')").fetchall()]


def manifest_sha256(manifest_path, root, raw_path):
    m = pd.read_csv(manifest_path, dtype=str).fillna("")
    row = m[m.relative_path == raw_path.relative_to(root).as_posix()]
    return row.iloc[0]["sha256"] if len(row) == 1 else None


def lineage_path(cache_path):
    return cache_path.with_name(cache_path.name + ".lineage.json")


def check_lineage(con, *, root, raw_path, cache_path, manifest_path, schema_version, expected_columns, source_sha256=None):
    """Return (ok, reason, lineage). Does not rebuild."""
    lp = lineage_path(cache_path)
    if not cache_path.exists():
        return False, "cache missing", None
    if not lp.exists():
        return False, "lineage file missing", None
    try:
        lin = json.loads(lp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "lineage file unreadable", None
    src = source_sha256 or sha256_file(raw_path)
    checks = [
        (lin.get("source_relative_path") == raw_path.relative_to(root).as_posix(), "source path differs"),
        (lin.get("source_sha256") == src, "raw source changed since the cache was built"),
        (lin.get("source_sha256") == manifest_sha256(manifest_path, root, raw_path), "lineage source differs from manifest"),
        (lin.get("schema_version") == schema_version, "schema version changed"),
        (lin.get("cache_sha256") == sha256_file(cache_path), "cache file changed since parity was verified"),
        (lin.get("raw_content_digest") == lin.get("cache_content_digest") and lin.get("raw_rows") == lin.get("cache_rows"),
         "lineage records failed parity"),
        (parquet_columns(con, cache_path) == expected_columns, "cache schema differs"),
    ]
    for ok, reason in checks:
        if not ok:
            return False, reason, lin
    return True, "current", lin


def ensure_cache(con, *, root, raw_path, cache_path, manifest_path, schema_version, expected_columns, build):
    """Reuse the cache if its lineage is current; otherwise rebuild and verify it.

    build(tmp_path) must write the cache to tmp_path. Returns a dict describing the
    outcome; 'ok' is False if the raw file does not match the manifest or parity fails.
    """
    source_sha = sha256_file(raw_path)
    man_sha = manifest_sha256(manifest_path, root, raw_path)
    result = {"cache": cache_path.relative_to(root).as_posix(), "source_matches_manifest": source_sha == man_sha}
    if source_sha != man_sha:
        return {**result, "ok": False, "action": "refused", "reason": "raw source does not match the manifest"}

    ok, reason, lin = check_lineage(con, root=root, raw_path=raw_path, cache_path=cache_path, manifest_path=manifest_path,
                                    schema_version=schema_version, expected_columns=expected_columns, source_sha256=source_sha)
    if ok:
        return {**result, "ok": True, "action": "reused", "reason": reason, "lineage": lin}

    print(f"  Cache not reusable ({reason}); rebuilding from raw: {raw_path.name}")
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    build(tmp)
    header, raw_rows, raw_digest = raw_csv_digest(raw_path)
    cache_rows, cache_digest = parquet_raw_digest(con, tmp, header)
    cols_ok = parquet_columns(con, tmp) == expected_columns
    if not (raw_rows == cache_rows and raw_digest == cache_digest and cols_ok):
        tmp.unlink(missing_ok=True)
        return {**result, "ok": False, "action": "rebuild_failed_parity", "reason": reason,
                "raw_rows": raw_rows, "cache_rows": cache_rows, "schema_ok": cols_ok}
    tmp.replace(cache_path)
    lin = {
        "source_relative_path": raw_path.relative_to(root).as_posix(),
        "source_sha256": source_sha,
        "source_bytes": raw_path.stat().st_size,
        "cache_relative_path": cache_path.relative_to(root).as_posix(),
        "cache_created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "schema_version": schema_version,
        "cache_columns": expected_columns,
        "raw_rows": raw_rows, "cache_rows": cache_rows,
        "raw_content_digest": raw_digest, "cache_content_digest": cache_digest,
        "digest_method": "sum mod 2^64 of first 8 bytes of sha256(row fields joined by U+001F); raw parsed with Python csv, cache read with DuckDB",
        "cache_sha256": sha256_file(cache_path),
    }
    lineage_path(cache_path).write_text(json.dumps(lin, indent=2), encoding="utf-8")
    return {**result, "ok": True, "action": "rebuilt", "reason": reason, "lineage": lin}
