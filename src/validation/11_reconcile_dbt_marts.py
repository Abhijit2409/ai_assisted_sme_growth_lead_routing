"""
Reconcile the dbt marts in the DuckDB warehouse with the validated analytical-view baseline.

For each of the 11 analytical relations, compares the dbt mart (analytics.<view> in
data/warehouse/sme_banking.duckdb) with the baseline Parquet view built by
src/analytics/09_build_analytical_views.py (data/processed/analytical_views/<view>.parquet):

- row count, column names, column order and data types;
- primary-key coverage, duplicate keys, null key parts and keys present on one side only;
- per-column null counts, distinct counts, numeric totals/min/max and category distributions;
- period, geography, industry and size-band coverage;
- full-row comparison in both directions (EXCEPT ALL, which treats NULLs as equal) and an
  order-independent row checksum.

Tolerance policy: every row-level value, key, count, distinct count, minimum, maximum and category
distribution is compared exactly (tolerance 0). The only tolerance is a relative 1e-12 on the SUM of
a DOUBLE column: DuckDB adds doubles in a thread-dependent order and floating-point addition is not
associative, so the same rows can sum to values differing in the last digits (observed: 5.4e-7 on a
total of 5.2e8 for the momentum `value` column, while the full-row comparison finds 0 differing rows).

It also summarises the latest dbt test run (target/run_results.json) and writes evidence to
data/validation/dbt_migration/. The baseline Parquet files and the warehouse are opened read-only.
Exits with status 1 if any model fails to reconcile or any dbt test did not pass.

Run from any directory with the analysis interpreter, after `dbt build` and `dbt test`:
    C:/Users/abhij/anaconda3/python.exe src/validation/11_reconcile_dbt_marts.py
"""

from pathlib import Path
import datetime as dt
import hashlib
import json
import sys

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
BASELINE = ROOT / "data" / "processed" / "analytical_views"
OUT = ROOT / "data" / "validation" / "dbt_migration"
RUN_RESULTS = ROOT / "target" / "run_results.json"
DBT_MANIFEST = ROOT / "target" / "manifest.json"
COMPILED = ROOT / "target" / "compiled"
DOCS = {"README.md": ROOT / "README.md", "docs/dbt_transformation_layer.md": ROOT / "docs" / "dbt_transformation_layer.md"}
# Each key test is rerun with one injected fault and must then report failing rows (proves the test can fail).
MUTATIONS = [("assert_seeds_match_reviewed_files", "nullif(columns(*), '')", "upper(columns(*))", "file side upper-cased"),
             ("assert_rates_use_same_source_active_denominator", "- interval 1 month", "- interval 2 month", "t-2 instead of t-1"),
             ("assert_net_formation_equals_openings_minus_closures", "net_formation is distinct from openings - closures",
              "net_formation is distinct from openings + closures", "openings plus closures")]
MANIFEST = BASELINE / "analytical_views_manifest.csv"

VIEWS = ["dim_cma", "dim_industry", "dim_size_band", "bridge_cma_vintage", "bridge_industry", "bridge_size_band",
         "fact_cma_momentum_monthly", "fact_cma_market_scale_snapshot", "fact_cma_sector_composition_snapshot",
         "fact_cma_size_composition_snapshot", "fact_ised_financing_context"]
PERIOD_COLS = ("ref_month", "snapshot_period", "source_ref_date", "survey_year")
GEO_COLS = ("cma_code_sgc2021", "cma_code_sgc2016", "segment_key")
INDUSTRY_COLS = ("industry_code", "industry_code_0722", "naics_sector", "source_code", "target_code")
SIZE_COLS = ("size_band_key", "size_scope", "source_band", "target_band")
NUMERIC = ("BIGINT", "INTEGER", "DOUBLE", "HUGEINT", "DECIMAL", "FLOAT")
SUM_RELATIVE_TOLERANCE = 1e-12  # only for SUM of DOUBLE columns; see module docstring


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = pd.read_csv(MANIFEST, dtype=str)
    pks = dict(zip(manifest.view_name, manifest.primary_key.str.split(", ")))
    baseline_hashes_before = {r.view_name: sha256_file(ROOT / r.output_path) for r in manifest.itertuples()}
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    models, schema_rows, metric_rows, key_rows = [], [], [], []

    for v in VIEWS:
        b = f"read_parquet('{(BASELINE / f'{v}.parquet').as_posix()}')"
        d = f"analytics.{v}"
        con.execute(f"create or replace temp table base as select * from {b}")
        con.execute(f"create or replace temp table mart as select * from {d}")
        sb = [(c, t) for c, t, *_ in con.execute("describe base").fetchall()]
        sd = [(c, t) for c, t, *_ in con.execute("describe mart").fetchall()]
        for i in range(max(len(sb), len(sd))):
            cb, tb = sb[i] if i < len(sb) else (None, None)
            cd, td = sd[i] if i < len(sd) else (None, None)
            schema_rows.append({"model": v, "position": i + 1, "baseline_column": cb, "dbt_column": cd, "baseline_type": tb, "dbt_type": td,
                                "name_matches": cb == cd, "type_matches": tb == td, "passed": cb == cd and tb == td})
        schema_ok = sb == sd
        rows_b, rows_d = con.execute("select (select count(*) from base), (select count(*) from mart)").fetchone()
        only_b = con.execute("select count(*) from (select * from base except all select * from mart)").fetchone()[0] if schema_ok else None
        only_d = con.execute("select count(*) from (select * from mart except all select * from base)").fetchone()[0] if schema_ok else None
        chk_b = con.execute("select sum(hash(t)::hugeint)::varchar from base t").fetchone()[0]
        chk_d = con.execute("select sum(hash(t)::hugeint)::varchar from mart t").fetchone()[0]

        pk = pks[v]
        on = " and ".join(f"b.{k} is not distinct from m.{k}" for k in pk)
        k = {}
        for side in ("base", "mart"):
            k[side] = con.execute(f"""select count(distinct ({', '.join(pk)})),
                                             (select count(*) from (select {', '.join(pk)} from {side} group by all having count(*) > 1)),
                                             count(*) filter (where {' or '.join(f'{c} is null' for c in pk)})
                                      from {side}""").fetchone()
        keys_only_b = con.execute(f"select count(*) from (select distinct {', '.join(pk)} from base) b where not exists (select 1 from mart m where {on})").fetchone()[0]
        keys_only_d = con.execute(f"select count(*) from (select distinct {', '.join(pk)} from mart) m where not exists (select 1 from base b where {on})").fetchone()[0]
        key_ok = k["base"] == k["mart"] and keys_only_b == 0 and keys_only_d == 0 and k["mart"][1] == 0
        key_rows.append({"model": v, "primary_key": ", ".join(pk), "baseline_distinct_keys": k["base"][0], "dbt_distinct_keys": k["mart"][0],
                         "baseline_duplicate_keys": k["base"][1], "dbt_duplicate_keys": k["mart"][1],
                         "baseline_rows_with_null_key_part": k["base"][2], "dbt_rows_with_null_key_part": k["mart"][2],
                         "keys_only_in_baseline": keys_only_b, "keys_only_in_dbt": keys_only_d, "passed": key_ok})

        metrics_ok = True
        for col, typ in sd if schema_ok else []:
            qc = f'"{col}"'
            stats = ["count(*) filter (where {c} is null)", "count(distinct {c})"]
            names = ["null_count", "distinct_count"]
            if typ.startswith(NUMERIC):
                stats += ["sum({c})::double", "min({c})::double", "max({c})::double"]
                names += ["sum", "min", "max"]
            else:
                stats += ["min({c}::varchar)", "max({c}::varchar)",
                          "md5(string_agg(coalesce({c}::varchar, '<NULL>') || '=' || n::varchar, '|' order by coalesce({c}::varchar, '<NULL>')))"]
                names += ["min", "max", "category_distribution_md5"]
            role = ("period" if col in PERIOD_COLS else "geography" if col in GEO_COLS else "industry" if col in INDUSTRY_COLS
                    else "size band" if col in SIZE_COLS else "measure" if typ.startswith(NUMERIC) else "attribute")
            vals = {}
            for side in ("base", "mart"):
                if typ.startswith(NUMERIC) or "category_distribution_md5" not in names:
                    sql = "select " + ", ".join(s.format(c=qc) for s in stats) + f" from {side}"
                else:
                    sql = ("select " + ", ".join(s.format(c=qc) for s in stats[:4]) + ", (select "
                           + stats[4].format(c=qc) + f" from (select {qc}, count(*) as n from {side} group by 1)) from {side}")
                vals[side] = con.execute(sql).fetchone()
            for name, vb, vd in zip(names, vals["base"], vals["mart"]):
                sum_of_double = name == "sum" and typ == "DOUBLE"
                tol = SUM_RELATIVE_TOLERANCE * max(abs(vb or 0), 1.0) if sum_of_double else 0.0
                if isinstance(vb, float) or isinstance(vd, float):
                    diff = abs(vb - vd) if vb is not None and vd is not None else (0.0 if vb is None and vd is None else None)
                    ok = diff is not None and diff <= tol
                else:
                    diff = 0 if vb == vd else None
                    ok = vb == vd
                metrics_ok &= ok
                metric_rows.append({"model": v, "column": col, "column_role": role, "data_type": typ, "metric": name,
                                    "baseline_value": vb, "dbt_value": vd, "absolute_difference": diff if diff is not None else "differs",
                                    "tolerance": tol,
                                    "tolerance_reason": ("relative 1e-12 on a SUM of doubles: summation order is thread-dependent "
                                                         "(row values are compared exactly by the full-row check)") if sum_of_double else "exact comparison",
                                    "passed": ok})
        passed = schema_ok and rows_b == rows_d and only_b == 0 and only_d == 0 and chk_b == chk_d and key_ok and metrics_ok
        models.append({"model": v, "baseline_rows": rows_b, "dbt_rows": rows_d, "baseline_columns": len(sb), "dbt_columns": len(sd),
                       "schema_matches": schema_ok, "rows_only_in_baseline": only_b, "rows_only_in_dbt": only_d,
                       "baseline_row_checksum": chk_b, "dbt_row_checksum": chk_d, "checksum_matches": chk_b == chk_d,
                       "keys_match": key_ok, "column_metrics_match": metrics_ok, "passed": passed})
        print(f"  [{'PASS' if passed else 'FAIL'}] {v}: {rows_d:,} rows; schema {schema_ok}; rows differing {only_b}/{only_d}; keys {key_ok}; metrics {metrics_ok}")

    baseline_hashes_after = {r.view_name: sha256_file(ROOT / r.output_path) for r in manifest.itertuples()}
    baseline_intact = baseline_hashes_before == baseline_hashes_after and all(
        baseline_hashes_after[r.view_name] == r.file_sha256 for r in manifest.itertuples())

    tests = {"available": RUN_RESULTS.exists()}
    if RUN_RESULTS.exists():
        rr = json.loads(RUN_RESULTS.read_text(encoding="utf-8"))
        dm_nodes = json.loads(DBT_MANIFEST.read_text(encoding="utf-8"))["nodes"] if DBT_MANIFEST.exists() else {}
        opp_nodes = {k for k, nd in dm_nodes.items() if "opportunity" in (nd.get("tags") or [])}
        # base analytical layer only: exclude market-opportunity framework tests (tagged, or depending on tagged nodes)
        is_opp = lambda uid: uid in opp_nodes or bool(set(dm_nodes.get(uid, {}).get("depends_on", {}).get("nodes", [])) & opp_nodes)  # noqa: E731
        res = [{"unique_id": r["unique_id"], "status": r["status"], "failures": r.get("failures"), "execution_time": round(r.get("execution_time", 0), 3)}
               for r in rr["results"] if not is_opp(r["unique_id"])]
        tests.update({"dbt_version": rr["metadata"]["dbt_version"], "generated_at": rr["metadata"]["generated_at"],
                      "command": " ".join(rr["args"].get("which", "").split()) if isinstance(rr.get("args"), dict) else "",
                      "tests": sum(1 for r in res if r["unique_id"].startswith("test.")),
                      "tests_passed": sum(1 for r in res if r["unique_id"].startswith("test.") and r["status"] == "pass"),
                      "singular_tests": sum(1 for r in res if r["unique_id"].startswith("test.") and ".assert_" in r["unique_id"]),
                      "results": res})
    tests_ok = tests.get("tests", 0) > 0 and tests.get("tests") == tests.get("tests_passed")

    sensitivity = []
    for name, original, fault, label in MUTATIONS:
        path = next(COMPILED.rglob(f"{name}.sql"), None)
        sql = path.read_text(encoding="utf-8") if path else ""
        as_written = con.execute(f"select count(*) from ({sql})").fetchone()[0] if original in sql else None
        injected = con.execute(f"select count(*) from ({sql.replace(original, fault)})").fetchone()[0] if original in sql else None
        sensitivity.append({"test": name, "injected_fault": label, "failing_rows_as_written": as_written, "failing_rows_with_fault": injected,
                            "passed": as_written == 0 and bool(injected)})
    sensitivity_ok = all(s["passed"] for s in sensitivity)
    for s in sensitivity:
        print(f"  [{'PASS' if s['passed'] else 'FAIL'}] test sensitivity {s['test']}: {s['failing_rows_as_written']} -> {s['failing_rows_with_fault']} ({s['injected_fault']})")

    dm = json.loads(DBT_MANIFEST.read_text(encoding="utf-8")) if DBT_MANIFEST.exists() else {"nodes": {}}
    nodes = dm["nodes"].values()
    models_by_layer = {layer: sum(1 for n in nodes if n["resource_type"] == "model" and n["fqn"][1] == layer)
                       for layer in ("staging", "intermediate", "marts")}
    seeds = [n for n in nodes if n["resource_type"] == "seed" and "opportunity" not in (n.get("tags") or [])]   # base-layer seeds only
    xw_seeds = sum(1 for n in seeds if n["original_file_path"].replace("\\", "/").startswith("data/reference/crosswalks/"))
    singular = sum(1 for n in nodes if n["resource_type"] == "test" and n["original_file_path"].replace("\\", "/").startswith("tests/singular/"))
    rows_by_model = {m["model"]: m["dbt_rows"] for m in models}
    claims = {
        "README.md": {"tests": f"base analytical layer: {tests.get('tests')} dbt tests"},
        "docs/dbt_transformation_layer.md": {
            "tests": f"The base analytical layer has **{tests.get('tests')} tests**",
            "singular": f"(`tests/singular/`, {singular} files)",
            "metrics": f"all {sum(r['passed'] for r in metric_rows)} column metrics pass",
            "crosswalk_seeds": f"{xw_seeds} reviewed crosswalks (read in place",
            "map_seeds": f"{len(seeds) - xw_seeds} reviewed code maps",
            "cells": f"({8118:,})" if False else "",
            **{f"mutation_{s['test']}": f"{s['failing_rows_with_fault']:,}" for s in sensitivity if s["failing_rows_with_fault"]},
            **{f"rows_{m}": f"| {r:,} |" for m, r in rows_by_model.items()},
        },
    }
    claims["docs/dbt_transformation_layer.md"] = {k: v for k, v in claims["docs/dbt_transformation_layer.md"].items() if v}
    doc_missing = {}
    for doc, cl in claims.items():
        text = DOCS[doc].read_text(encoding="utf-8") if DOCS[doc].exists() else ""
        for k, v in cl.items():
            if k.startswith("rows_"):
                line = next((ln for ln in text.splitlines() if ln.startswith(f"| `{k[5:]}` |")), "")
                if v not in line:
                    doc_missing[f"{doc}:{k}"] = v
            elif v not in text:
                doc_missing[f"{doc}:{k}"] = v
    diffs_small = all(r["absolute_difference"] in (0, 0.0) or (isinstance(r["absolute_difference"], float) and r["absolute_difference"] < 1e-6)
                      for r in metric_rows if r["tolerance"])
    docs_ok = not doc_missing and diffs_small and "less than 1e-6" in DOCS["docs/dbt_transformation_layer.md"].read_text(encoding="utf-8")
    print(f"  [{'PASS' if docs_ok else 'FAIL'}] documentation numbers reproduced ({sum(len(c) for c in claims.values()) - len(doc_missing)} of "
          f"{sum(len(c) for c in claims.values())} claims)" + (f"; missing {doc_missing}" if doc_missing else ""))

    pd.DataFrame(models).to_csv(OUT / "model_reconciliation.csv", index=False, encoding="utf-8", lineterminator="\n")
    pd.DataFrame(schema_rows).to_csv(OUT / "schema_comparison.csv", index=False, encoding="utf-8", lineterminator="\n")
    pd.DataFrame(metric_rows).to_csv(OUT / "metric_comparison.csv", index=False, encoding="utf-8", lineterminator="\n")
    pd.DataFrame(key_rows).to_csv(OUT / "key_comparison.csv", index=False, encoding="utf-8", lineterminator="\n")
    (OUT / "dbt_test_results.json").write_text(json.dumps(tests, indent=2), encoding="utf-8", newline="\n")
    ok_models = sum(m["passed"] for m in models)
    summary = {
        "purpose": "Reconciliation of the dbt marts with the validated analytical-view baseline",
        "run_timestamp_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "warehouse": WAREHOUSE.relative_to(ROOT).as_posix(), "warehouse_schema": "analytics",
        "baseline": BASELINE.relative_to(ROOT).as_posix(), "duckdb_version": duckdb.__version__,
        "models_reconciled": ok_models, "models_total": len(models),
        "columns_compared": len(schema_rows), "column_metrics_compared": len(metric_rows),
        "metric_checks_passed": sum(r["passed"] for r in metric_rows),
        "tolerance_policy": "exact for rows, keys, counts, distinct counts, min, max and category distributions; relative 1e-12 only on SUM of DOUBLE columns (thread-dependent summation order)",
        "tolerance_applied": [{"model": r["model"], "column": r["column"], "metric": r["metric"], "absolute_difference": r["absolute_difference"],
                               "tolerance": r["tolerance"]} for r in metric_rows if r["tolerance"] and r["absolute_difference"] not in (0, 0.0)],
        "differences_documented": [] if ok_models == len(models) else [m["model"] for m in models if not m["passed"]],
        "baseline_parquet_unchanged": baseline_intact,
        "dbt_tests": {k_: tests.get(k_) for k_ in ("dbt_version", "tests", "tests_passed", "singular_tests", "generated_at")},
        "dbt_project": {"models_by_layer": models_by_layer, "seeds": len(seeds), "crosswalk_seeds": xw_seeds, "singular_test_files": singular},
        "test_sensitivity": sensitivity,
        "documentation_claims": claims, "documentation_claims_missing": doc_missing,
        "all_checks_pass": ok_models == len(models) and baseline_intact and tests_ok and sensitivity_ok and docs_ok,
    }
    (OUT / "migration_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8", newline="\n")
    print(f"\nModels reconciled: {ok_models} of {len(models)}; metric checks {summary['metric_checks_passed']} of {len(metric_rows)}; "
          f"dbt tests {tests.get('tests_passed')} of {tests.get('tests')}; baseline unchanged: {baseline_intact}")
    con.close()
    if not summary["all_checks_pass"]:
        print("RECONCILIATION FAILED")
        sys.exit(1)
    print("RECONCILIATION PASSED")


if __name__ == "__main__":
    main()
