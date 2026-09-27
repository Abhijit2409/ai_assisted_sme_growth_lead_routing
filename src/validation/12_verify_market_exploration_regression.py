"""
Regression and reproducibility check for the market exploration after it was connected to the dbt marts.

Steps:
1. Load the baseline recorded before the connection (data/validation/dbt_migration/market_exploration_baseline.json):
   output hashes, 28 indicator classifications, 33 findings, 7 figure records and the summary facts.
2. Run src/analysis/10_explore_market_opportunity.py twice into scratch roots under target/ (never
   the protected folders) and require exit status 0 both times.
3. Compare the two scratch runs with each other (determinism) and with the baseline (regression):
   CSV and PNG files byte for byte; Parquet by content (schema, rows, keys, values, nulls, order);
   classifications, findings, figure labels/units/periods and every summary fact; all controls pass.
4. Only if everything matches, run the exploration in place and confirm that every protected output
   except its run summary (timestamp, input list, control names) is byte-identical to the baseline.

Writes data/validation/dbt_migration/market_exploration_regression.csv and .json.
Exits with status 1 on any difference.

    C:/Users/abhij/anaconda3/python.exe src/validation/12_verify_market_exploration_regression.py
"""

from pathlib import Path
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXPLORER = ROOT / "src" / "analysis" / "10_explore_market_opportunity.py"
EVIDENCE = ROOT / "data" / "validation" / "dbt_migration"
BASELINE = EVIDENCE / "market_exploration_baseline.json"
SCRATCH = ROOT / "target" / "market_exploration_regression"
OUT_REL = Path("data") / "analysis" / "market_exploration"
FIG_REL = Path("outputs") / "phase_h_market_exploration"
SUMMARY_NAME = "phase_h_summary.json"
FORBIDDEN = ("score", "weight", "rank", "priorit")
# "recommend" is allowed only in two required, non-market columns: the indicator status of the candidate register
# and the suggested data treatment of outliers. No column may recommend or prioritise a market.
ALLOWED_RECOMMEND_COLUMNS = {"recommended_status", "outlier_treatment_recommendation"}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_explorer(output_root=None):
    cmd = [sys.executable, str(EXPLORER)] + (["--output-root", str(output_root)] if output_root else [])
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    tail = [ln for ln in r.stdout.splitlines() if "Gates:" in ln or "FAILED" in ln]
    return r.returncode, tail


def files_of(root):
    return {p.relative_to(root).as_posix(): p for d in (root / OUT_REL, root / FIG_REL) if d.exists() for p in sorted(d.iterdir()) if p.is_file()}


def parquet_equal(a, b):
    x, y = pd.read_parquet(a), pd.read_parquet(b)
    try:
        pd.testing.assert_frame_equal(x, y, check_dtype=True, check_exact=True)
        return True, f"{len(x):,} rows x {x.shape[1]} columns identical (schema, values, nulls, order)"
    except AssertionError as e:
        return False, str(e)[:300]


def compare(a_files, b_files, label):
    rows = []
    for rel in sorted(set(a_files) | set(b_files)):
        a, b = a_files.get(rel), b_files.get(rel)
        if rel.endswith(SUMMARY_NAME):
            continue
        if a is None or b is None:
            rows.append({"comparison": label, "file": rel, "method": "presence", "passed": False, "detail": "missing on one side"})
            continue
        same_bytes = sha(a) == sha(b)
        if same_bytes:
            rows.append({"comparison": label, "file": rel, "method": "byte-for-byte", "passed": True, "detail": sha(a)})
        elif rel.endswith(".parquet"):
            ok, detail = parquet_equal(a, b)
            rows.append({"comparison": label, "file": rel, "method": "content (Parquet bytes differ)", "passed": ok, "detail": detail})
        else:
            rows.append({"comparison": label, "file": rel, "method": "byte-for-byte", "passed": False, "detail": "bytes differ"})
    return rows


def summary_facts(p):
    s = json.loads(p.read_text(encoding="utf-8"))
    return s, s.get("facts", {})


def main():
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    base_files = {rel: ROOT / rel for rel in base["file_sha256"]}
    checks, rows = {}, []

    shutil.rmtree(SCRATCH, ignore_errors=True)
    runs = {}
    for name in ("run1", "run2"):
        root = SCRATCH / name
        code, tail = run_explorer(root)
        runs[name] = root
        checks[f"{name}_exit_status_0"] = code == 0
        print(f"  {name}: exit {code}; {' | '.join(tail)}")

    # baseline files recorded against the project root; compare them with each scratch run
    base_rel = {rel: ROOT / rel for rel in base_files}
    for name, root in runs.items():
        scratch = {rel: root / rel for rel in base_rel if (root / rel).exists()}
        rows += compare(base_rel, scratch, f"baseline vs {name}")
    rows += compare(files_of(runs["run1"]), files_of(runs["run2"]), "run1 vs run2")
    checks["all_outputs_match_baseline_and_between_runs"] = all(r["passed"] for r in rows)

    s1, f1 = summary_facts(runs["run1"] / OUT_REL / SUMMARY_NAME)
    s2, f2 = summary_facts(runs["run2"] / OUT_REL / SUMMARY_NAME)
    checks["all_controls_pass_in_both_runs"] = s1.get("all_gates_pass") is True and s2.get("all_gates_pass") is True
    checks["at_least_54_controls"] = len(s1["gates"]) >= base["gates_total"] and len(s2["gates"]) == len(s1["gates"])
    checks["summary_facts_equal_baseline"] = f1 == base["facts"] and f2 == base["facts"]
    reg = pd.read_csv(runs["run1"] / OUT_REL / "candidate_indicator_register.csv")
    checks["28_indicators_present_classifications_unchanged"] = len(reg) == 28 and dict(zip(reg.indicator_id, reg.recommended_status)) == base["indicator_classifications"]
    fnd = pd.read_csv(runs["run1"] / OUT_REL / "descriptive_findings.csv")
    checks["33_findings_unchanged"] = len(fnd) == 33 and dict(zip(fnd.finding_id, fnd.finding_statement)) == base["findings"]
    fig = pd.read_csv(runs["run1"] / OUT_REL / "figure_register.csv")
    checks["7_figures_same_labels_units_periods"] = len(fig) == 7 and fig.to_dict("records") == base["figures"]
    cols = [c.lower() for f in (runs["run1"] / OUT_REL).glob("*.csv") for c in pd.read_csv(f, nrows=0).columns]
    checks["no_score_weight_rank_or_recommendation_column"] = (not any(t in c for c in cols for t in FORBIDDEN)
                                                              and all(c in ALLOWED_RECOMMEND_COLUMNS for c in cols if "recommend" in c))
    expected_names = (set(base.get("gate_names", [])) - {"reads_only_views_and_validated_summaries"}) | {
        "reads_only_dbt_marts_and_validated_summaries", "dbt_marts_tested_and_reconciled_with_baseline"}
    checks["original_54_controls_preserved_by_name"] = len(base.get("gate_names", [])) == 54 and set(s1["gates"]) == expected_names == set(s2["gates"])
    control_changes = {"replaced": ["reads_only_views_and_validated_summaries -> reads_only_dbt_marts_and_validated_summaries"],
                       "added": ["dbt_marts_tested_and_reconciled_with_baseline"],
                       "unexpected_differences": sorted(set(s1["gates"]) ^ expected_names)}

    in_place = {}
    if all(checks.values()):
        code, tail = run_explorer(None)
        checks["in_place_run_exit_status_0"] = code == 0
        now = {rel: sha(ROOT / rel) for rel in base["file_sha256"] if not rel.endswith(SUMMARY_NAME)}
        changed = [rel for rel, h in now.items() if h != base["file_sha256"][rel]]
        parquet_ok = all(parquet_equal(ROOT / rel, runs["run1"] / rel)[0] for rel in changed if rel.endswith(".parquet"))
        checks["protected_outputs_identical_after_in_place_run"] = not [r for r in changed if not r.endswith(".parquet")] and parquet_ok
        s_now = json.loads((ROOT / OUT_REL / SUMMARY_NAME).read_text(encoding="utf-8"))
        checks["in_place_summary_all_controls_pass"] = s_now.get("all_gates_pass") is True and s_now.get("facts") == base["facts"]
        in_place = {"exit": code, "changed_files_other_than_summary": changed, "controls": f"{sum(s_now['gates'].values())} of {len(s_now['gates'])}",
                    "summary_differences": "run timestamp, elapsed time, input list (dbt marts instead of Parquet views) and control names "
                                           "(reads_only_views_and_validated_summaries -> reads_only_dbt_marts_and_validated_summaries; "
                                           "added dbt_marts_tested_and_reconciled_with_baseline)"}
    else:
        print("  regression differences found: protected outputs NOT regenerated")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(EVIDENCE / "market_exploration_regression.csv", index=False, encoding="utf-8", lineterminator="\n")
    result = {"run_timestamp_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
              "baseline_controls": f"{base['gates_passing']} of {base['gates_total']}",
              "connected_controls": f"{sum(s1['gates'].values())} of {len(s1['gates'])}",
              "files_compared": len(rows), "files_matching": sum(r["passed"] for r in rows),
              "byte_identical": sum(r["method"] == "byte-for-byte" and r["passed"] for r in rows),
              "content_identical_parquet": sum(r["method"].startswith("content") and r["passed"] for r in rows),
              "control_changes": control_changes, "control_names": sorted(s1["gates"]),
              "checks": checks, "in_place_run": in_place, "all_checks_pass": all(checks.values())}
    (EVIDENCE / "market_exploration_regression.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8", newline="\n")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print(f"\nFiles compared: {result['files_compared']} ({result['byte_identical']} byte-identical, {result['content_identical_parquet']} Parquet content-identical)")
    if not result["all_checks_pass"]:
        print("MARKET-EXPLORATION REGRESSION FAILED")
        sys.exit(1)
    print("MARKET-EXPLORATION REGRESSION PASSED")


if __name__ == "__main__":
    main()
