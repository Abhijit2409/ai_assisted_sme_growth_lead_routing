"""
Read-only verification of every input the analytical pipeline depends on. It writes nothing.

Checks:
1. every raw source file matches its SHA-256 in data/source_manifest.csv;
2. both StatCan cache lineage records are current (cache_lineage.check_lineage; a stale cache
   fails here and is never rebuilt by the pipeline);
3. all source-validation, crosswalk, scope, analytical-layer and market-exploration summaries pass;
4. the baseline analytical views match their manifest checksums;
5. the market-exploration outputs match the baseline recorded before the dbt connection
   (the run summary is excluded because it records its own timestamp and inputs).

Exits with status 1 on any failure.

    C:/Users/abhij/anaconda3/python.exe src/validation/verify_pipeline_inputs.py
"""

from pathlib import Path
import hashlib
import json
import sys

sys.dont_write_bytecode = True
import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "validation"))
from cache_lineage import CACHE_SCHEMAS, check_lineage, expected_columns  # noqa: E402

DATA = ROOT / "data"
MANIFEST = DATA / "source_manifest.csv"
CACHES = {"statcan_33100722": (DATA / "raw" / "statcan_33100722" / "33100722-eng" / "33100722.csv", DATA / "cache" / "statcan_33100722.parquet"),
          "statcan_33101097": (DATA / "raw" / "statcan_33101097" / "33101097-eng" / "33101097.csv", DATA / "cache" / "statcan_33101097.parquet")}
SUMMARIES = ["data/validation/33100722/phase_a_summary.json", "data/validation/33101097/phase_b_summary.json",
             "data/validation/ised_2023/phase_c_summary.json", "data/validation/source_comparison/phase_d_summary.json",
             "data/validation/crosswalks/phase_e_summary.json", "data/validation/mvp_grain/phase_f_summary.json",
             "data/validation/analytical_views/phase_g_summary.json", "data/analysis/market_exploration/phase_h_summary.json"]
VIEW_MANIFEST = DATA / "processed" / "analytical_views" / "analytical_views_manifest.csv"
EXPLORATION_BASELINE = DATA / "validation" / "dbt_migration" / "market_exploration_baseline.json"

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    manifest = pd.read_csv(MANIFEST, dtype=str).fillna("")
    shas = {r.relative_path: sha256_file(ROOT / r.relative_path) for r in manifest.itertuples()}
    check("raw source files match data/source_manifest.csv", all(shas[r.relative_path] == r.sha256 for r in manifest.itertuples()),
          f"{len(manifest)} files")
    con = duckdb.connect()
    for key, (raw, cache) in CACHES.items():
        ok, why, _ = check_lineage(con, root=ROOT, raw_path=raw, cache_path=cache, manifest_path=MANIFEST,
                                   schema_version=CACHE_SCHEMAS[key]["version"], expected_columns=expected_columns(key, raw),
                                   source_sha256=shas[raw.relative_to(ROOT).as_posix()])
        check(f"cache lineage current: {key}", ok, why)
    con.close()
    for rel in SUMMARIES:
        p = ROOT / rel
        s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        g = s.get("gates", {})
        check(f"validation summary passes: {rel}", bool(g) and all(g.values()) and s.get("all_gates_pass") is True, f"{sum(g.values())} of {len(g)}")
    views = pd.read_csv(VIEW_MANIFEST, dtype=str)
    check("baseline analytical views match their manifest", all(sha256_file(ROOT / r.output_path) == r.file_sha256 for r in views.itertuples()),
          f"{len(views)} views")
    if EXPLORATION_BASELINE.exists():
        base = json.loads(EXPLORATION_BASELINE.read_text(encoding="utf-8"))
        diff = [rel for rel, h in base["file_sha256"].items() if not rel.endswith("phase_h_summary.json") and sha256_file(ROOT / rel) != h]
        check("market-exploration outputs match the recorded baseline", not diff, f"{len(base['file_sha256']) - 1} files" + (f"; differ: {diff}" if diff else ""))
    else:
        check("market-exploration baseline recorded", False, "data/validation/dbt_migration/market_exploration_baseline.json missing")
    if not all(results):
        print("INPUT VERIFICATION FAILED")
        sys.exit(1)
    print("INPUT VERIFICATION PASSED")


if __name__ == "__main__":
    main()
