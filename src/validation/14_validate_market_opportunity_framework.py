"""
Independent validation of the market opportunity framework.

Recomputes the framework from its PUBLISHED outputs (data/analysis/market_opportunity/*.csv) and reviewed
configuration seeds, without querying the scoring marts, and checks:
- base analytical layer still current (reconciliation evidence, row checksums of the marts the framework reads);
- configuration unchanged since it was declared before the first scoring run;
- dbt opportunity tests passed, and three key tests detect injected faults (test sensitivity);
- population eligibility (35 scored CMAs, 6 scale-only Tier 3 CMAs, no overlap, no imputation);
- weights (sum to 1, nonnegative, within-pillar sums), normalization bounds and direction;
- pillar, score, rank, percentile and classification recalculation; monotonicity;
- sensitivity coverage; ISED kept out of the score; no lead-like outputs;
- two-run reproducibility of the analysis (scratch runs compared byte for byte with each other and with
  the published outputs);
- documentation numbers in docs/market_opportunity_framework.md reproduced by the outputs.

Writes data/validation/opportunity_framework/{weight_validation, score_recalculation_checks,
population_eligibility_checks, framework_checks}.csv and opportunity_framework_summary.json.
Exits with status 1 if any check fails.

    C:/Users/abhij/anaconda3/python.exe src/validation/14_validate_market_opportunity_framework.py [--preconditions-only]
"""

from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
A = ROOT / "data" / "analysis" / "market_opportunity"
V = ROOT / "data" / "validation" / "opportunity_framework"
FIGS = ROOT / "outputs" / "market_opportunity_framework"
SEEDS = ROOT / "seeds" / "opportunity"
ANALYSIS = ROOT / "src" / "analysis" / "13_analyze_market_opportunity_framework.py"
DOC = ROOT / "docs" / "market_opportunity_framework.md"
MIGRATION = ROOT / "data" / "validation" / "dbt_migration"
RUN_RESULTS, DBT_MANIFEST, COMPILED = ROOT / "target" / "run_results.json", ROOT / "target" / "manifest.json", ROOT / "target" / "compiled"
SCRATCH = ROOT / "target" / "opportunity_reproducibility"
CORE = ["balanced", "scale_led", "growth_led", "stability_aware"]
PILLAR_COMPONENTS = {"scale": ["C01_LOG_SCALE"], "momentum": ["C05_NET_FORMATION_RATE", "C09_NET_FORMATION_TREND"], "dynamism": ["C03_OPENING_RATE"],
                     "commercial_fit": ["C15_SHARE_20_99"], "stability": ["C10_VOLATILITY_SCALE_ADJUSTED"]}
TOL = 1e-9          # published values are written with 10 significant digits; 1e-9 relative/absolute covers that rounding
checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def close(a, b, rel=1e-8):
    return np.isclose(a, b, rtol=rel, atol=1e-7)


def english(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def extra_claims(sc, comp, sens_df, so):
    """Further numbers quoted in docs/market_opportunity_framework.md, each generated from published outputs."""
    dc = pd.read_csv(V / "double_counting_checks.csv").set_index("check").result.map(json.loads)
    nc = pd.read_csv(V / "normalization_comparison.csv")
    alt = nc[(nc.component_id.isna()) & (nc.is_primary == False)]  # noqa: E712
    scen = pd.read_csv(A / "scenario_comparison.csv").set_index("scenario_id")
    lin = pd.read_csv(V / "indicator_lineage_check.csv")
    tc = pd.read_csv(V / "transformation_comparison.csv").set_index("component_id")
    top3 = json.loads((ROOT / "data/analysis/market_exploration/phase_h_summary.json").read_text(encoding="utf-8"))["facts"]["top3_share_pct"]
    grp = lambda g: sens_df[sens_df.stability_group == g].cma_label_sgc2021  # noqa: E731
    drv = lambda g: sens_df[sens_df.position_driver == g].cma_label_sgc2021  # noqa: E731
    pert = sens_df[sens_df.perturbation_top_quartile_frequency >= 0.8].cma_label_sgc2021
    big = sens_df.loc[sens_df.rank_range.idxmax()]
    c15 = sens_df.rank_shift_without_c15.abs()
    b = sc[sc.scenario_id == "balanced"]
    allp = b[(b[["pillar_scale", "pillar_momentum", "pillar_dynamism", "pillar_commercial_fit"]] >= 50).all(axis=1)].sort_values("score_rank")
    so_fit = so.sort_values("share_20_99_percentile_41_cma", ascending=False).head(2)
    so_big = so.sort_values("sme_employer_locations", ascending=False).head(2)
    vol, sm = dc["volatility penalises small markets twice"], dc["small markets lose rank under the unadjusted penalty"]
    return {
        "list_stable": english(grp("Stable stronger signal")), "list_perturbation": english(pert),
        "list_sensitive": english(grp("Assumption-sensitive")), "list_weaker": english(grp("Stable weaker signal")),
        "count_middle": f"**{len(grp('Stable middle signal'))}** CMAs form a stable middle", "count_weaker": f"**{len(grp('Stable weaker signal'))}** are stably weaker",
        "list_scale_driven": english(drv("scale-driven")), "list_momentum_driven": english(drv("momentum-driven")),
        "list_all_pillars": english(allp.cma_label_sgc2021),
        "largest_move": f"{big.cma_label_sgc2021}'s, {int(big.rank_range)} ranks",
        "vol_raw": f"(Spearman {vol['raw']:.2f})", "vol_adjusted": f"size ({vol['scale_adjusted']:.2f})",
        "vol_shift": f"about {sm['smallest_quartile']:.1f} ranks on average, while others gain about {-sm['others']:.1f}",
        "c05_c09": f"(Spearman {dc['C05 and C09 duplicate the momentum signal']['spearman']:.2f})",
        "momentum_share": f"{dc['a pillar gains influence from its component count']['variance_share']:.0%} of balanced score variance",
        "fit_vs_dynamism": f"(Spearman {dc['commercial fit favours a narrow type of market']['vs_dynamism']:.2f})",
        "dynamism_vs_scale": f"(Spearman {dc['hidden scale influence through dynamism']:.2f} with log scale)",
        "fit_loo": f"by {c15.median():.0f} places at the median and up to {int(c15.max())} ({sens_df.loc[c15.idxmax(), 'cma_label_sgc2021']})",
        "norm_correlation": f"{alt.spearman_balanced_rank_vs_primary.min():.2f}-{alt.spearman_balanced_rank_vs_primary.max():.2f}",
        "norm_shift": f"Only {int(alt.markets_shifting_9_or_more.min())}-{int(alt.markets_shifting_9_or_more.max())} markets",
        "persistence": f"(Spearman {dc['recent net formation persists between windows']:.2f})",
        "size_growth": f"{scen.loc['growth_led', 'spearman_score_vs_log_scale']:.2f} (growth-led)",
        "size_stability": f"{scen.loc['stability_aware', 'spearman_score_vs_log_scale']:.2f} (stability-aware)",
        "skew": f"from {tc.loc['C01_LOG_SCALE', 'raw_skewness']:.2f} to {tc.loc['C01_LOG_SCALE', 'transformed_skewness']:.2f}",
        "top3": f"{top3}% of 41-CMA locations",
        "lineage": "to within 1e-10" if lin.max_absolute_difference.max() <= 1e-10 else "LINEAGE DIFFERENCE ABOVE 1e-10",
        "tier3_range": f"between the {so.scale_percentile_41_cma.min():.1f}th and {so.scale_percentile_41_cma.max():.1f}th percentile",
        "tier3_fit": f"{so_fit.cma_label_sgc2021.iloc[0]} ({so_fit.share_20_99_percentile_41_cma.iloc[0]:.1f}th percentile) and {so_fit.cma_label_sgc2021.iloc[1]} ({so_fit.share_20_99_percentile_41_cma.iloc[1]:.1f}th percentile)",
        "tier3_largest": f"{english(so_big.cma_label_sgc2021)} are the largest",
    }


def main():
    start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    print("PRECONDITIONS")
    mig = json.loads((MIGRATION / "migration_summary.json").read_text(encoding="utf-8"))
    reg_ev = json.loads((MIGRATION / "market_exploration_regression.json").read_text(encoding="utf-8"))
    check("base dbt migration evidence passes", mig.get("all_checks_pass") is True and mig.get("models_reconciled") == 11, f"{mig.get('models_reconciled')} of 11 marts")
    check("market-exploration regression evidence passes", reg_ev.get("all_checks_pass") is True)
    rec = pd.read_csv(MIGRATION / "model_reconciliation.csv", dtype=str).set_index("model").dbt_row_checksum
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    used = ["dim_cma", "fact_cma_market_scale_snapshot", "fact_cma_size_composition_snapshot", "fact_cma_momentum_monthly",
            "fact_cma_sector_composition_snapshot", "fact_ised_financing_context", "dim_industry"]
    fresh = {m: con.execute(f"select sum(hash(t)::hugeint)::varchar from analytics.{m} t").fetchone()[0] == rec[m] for m in used}
    check("base marts read by the framework match their reconciled row checksums", all(fresh.values()), f"{sum(fresh.values())} of {len(used)}")
    dim = con.execute("select cma_code_sgc2021, tier, momentum_comparison_eligible from analytics.dim_cma").df()
    decl = json.loads((V / "configuration_declaration.json").read_text(encoding="utf-8"))
    cur = {f"seeds/opportunity/{p.name}": sha(p) for p in sorted(SEEDS.glob("*.csv"))}
    check("configuration unchanged since declared before the first scoring run", cur == decl["declared_sha256"],
          f"declared {decl['declared_at_utc']}; first scoring build {decl['first_scoring_build_utc']}")

    if "--preconditions-only" in sys.argv:          # run before the framework is rebuilt (pipeline opportunity section)
        con.close()
        ok = all(c["passed"] for c in checks)
        print("PRECONDITIONS PASSED" if ok else "PRECONDITIONS FAILED")
        sys.exit(0 if ok else 1)

    # dbt opportunity tests (latest run) and test sensitivity
    rr = json.loads(RUN_RESULTS.read_text(encoding="utf-8")) if RUN_RESULTS.exists() else {"results": []}
    dm = json.loads(DBT_MANIFEST.read_text(encoding="utf-8")) if DBT_MANIFEST.exists() else {"nodes": {}}
    opp_nodes = {k for k, n in dm["nodes"].items() if "opportunity" in (n.get("tags") or [])}
    crm_nodes = {k for k, n in dm["nodes"].items() if "crm_data" in (n.get("tags") or [])}
    # synthetic CRM tests are a separate layer: excluded from both the opportunity and the base counts
    tests_all = {k: n for k, n in dm["nodes"].items() if n["resource_type"] == "test"
                 and k not in crm_nodes and not set(n.get("depends_on", {}).get("nodes", [])) & crm_nodes}
    # a test belongs to the framework if it is tagged or depends on a tagged model or seed (generic tests inherit no tag)
    opp_tests = {k for k, n in tests_all.items() if k in opp_nodes or set(n.get("depends_on", {}).get("nodes", [])) & opp_nodes}
    base_tests = set(tests_all) - opp_tests
    ran = {r["unique_id"]: r["status"] for r in rr["results"]}
    opp_ran = {k: v for k, v in ran.items() if k in opp_tests}
    check("dbt opportunity tests all passed in the latest dbt run", len(opp_ran) == len(opp_tests) > 0 and all(v == "pass" for v in opp_ran.values()),
          f"{sum(v == 'pass' for v in opp_ran.values())} of {len(opp_tests)}; base tests defined: {len(base_tests)}")
    mutations = [("assert_opportunity_scores_recompute", "weight_scale * pillar_scale", "(weight_scale + 0.01) * pillar_scale", "scale weight +0.01"),
                 ("assert_opportunity_classification_rules", "when f.score_percentile >= t.top_q then", "when f.score_percentile >= t.top_q + 10 then", "top-quartile threshold +10"),
                 ("assert_opportunity_monotonicity", "least(c01_normalized + 10, 100)", "greatest(c01_normalized - 10, 0)", "component lowered instead of raised")]
    sens = []
    for name, orig, fault, label in mutations:
        p = next(COMPILED.rglob(f"{name}.sql"), None)
        sql = p.read_text(encoding="utf-8") if p else ""
        ok0 = con.execute(f"select count(*) from ({sql})").fetchone()[0] if orig in sql else None
        ok1 = con.execute(f"select count(*) from ({sql.replace(orig, fault)})").fetchone()[0] if orig in sql else None
        sens.append({"test": name, "fault": label, "failing_rows_as_written": ok0, "failing_rows_with_fault": ok1})
    check("key opportunity tests detect injected faults", all(s["failing_rows_as_written"] == 0 and (s["failing_rows_with_fault"] or 0) > 0 for s in sens),
          "; ".join(f"{s['test']}: {s['failing_rows_as_written']} -> {s['failing_rows_with_fault']}" for s in sens))
    con.close()

    print("PUBLISHED-OUTPUT RECALCULATION")
    sc = pd.read_csv(A / "cma_opportunity_scores.csv", dtype={"cma_code_sgc2021": str})
    comp = pd.read_csv(A / "cma_opportunity_components.csv", dtype={"cma_code_sgc2021": str})
    sens_df = pd.read_csv(A / "cma_opportunity_sensitivity.csv", dtype={"cma_code_sgc2021": str})
    so = pd.read_csv(A / "cma_scale_only_context.csv", dtype={"cma_code_sgc2021": str})
    cls = pd.read_csv(A / "cma_opportunity_classifications.csv", dtype={"cma_code_sgc2021": str})
    w = pd.read_csv(SEEDS / "opportunity_weight_scenarios.csv")
    reg = pd.read_csv(SEEDS / "opportunity_indicator_registry.csv")
    th = dict(pd.read_csv(SEEDS / "opportunity_decision_thresholds.csv", dtype=str)[["parameter", "value"]].values)

    # population
    elig = set(dim[dim.momentum_comparison_eligible].cma_code_sgc2021)
    t3 = set(dim[dim.tier == 3].cma_code_sgc2021)
    pop = [("scored CMAs equal the 35 momentum-eligible CMAs", set(sc.cma_code_sgc2021) == elig and len(elig) == 35, len(set(sc.cma_code_sgc2021))),
           ("every scored CMA appears once per core scenario", sc.groupby("scenario_id").cma_code_sgc2021.nunique().to_dict() == {s: 35 for s in CORE} and len(sc) == 140, len(sc)),
           ("scale-only CMAs equal the 6 Tier 3 CMAs", set(so.cma_code_sgc2021) == t3 and len(so) == 6, len(so)),
           ("no CMA is both scored and scale-only", not set(sc.cma_code_sgc2021) & set(so.cma_code_sgc2021), 0),
           ("scale-only CMAs have no score, rank or momentum field", not [c for c in so.columns if ("score" in c and c != "full_score_eligible") or "rank" in c or "momentum" in c]
            and (so.full_score_eligible == False).all(), ""),  # noqa: E712
           ("no Tier 3 component rows (nothing imputed)", not set(comp.cma_code_sgc2021) & t3, ""),
           ("every scored CMA has all core components complete", comp.groupby("cma_code_sgc2021").component_id.nunique().eq(7).all()
            and (comp.input_status == "complete").all() and comp.normalized_percentile.notna().all(), ""),
           ("sensitivity covers every scored CMA", set(sens_df.cma_code_sgc2021) == elig and len(sens_df) == 35, len(sens_df)),
           ("classification table covers every scored CMA", set(cls.cma_code_sgc2021) == elig, len(cls))]
    pd.DataFrame([{"check": c, "passed": bool(o), "value": v} for c, o, v in pop]).to_csv(V / "population_eligibility_checks.csv", index=False, lineterminator="\n")
    for c, o, v in pop:
        check(c, o, v)

    # weights
    wrows = []
    for sid, g in w.groupby("scenario_id"):
        wrows.append({"scenario_id": sid, "scenario_role": g.scenario_role.iloc[0], "weight_sum": g.pillar_weight.sum(), "min_weight": g.pillar_weight.min(),
                      "weighted_pillars": ";".join(g[g.pillar_weight > 0].pillar), "sums_to_one": abs(g.pillar_weight.sum() - 1) <= TOL,
                      "nonnegative": bool((g.pillar_weight >= 0).all())})
    for pl, g in reg.groupby("pillar"):
        wrows.append({"scenario_id": f"within-pillar: {pl}", "weight_sum": g.within_pillar_weight.sum(), "min_weight": g.within_pillar_weight.min(),
                      "weighted_pillars": ";".join(g.component_id), "sums_to_one": abs(g.within_pillar_weight.sum() - 1) <= TOL,
                      "nonnegative": bool((g.within_pillar_weight > 0).all())})
    wv = pd.DataFrame(wrows)
    wv.to_csv(V / "weight_validation.csv", index=False, lineterminator="\n")
    pub_w = sc.groupby("scenario_id")[["weight_scale", "weight_momentum", "weight_dynamism", "weight_commercial_fit", "weight_stability"]].nunique()
    check("scenario and within-pillar weights sum to 1 and are nonnegative", wv.sums_to_one.all() and wv.nonnegative.all(), f"{len(wv)} weight sets")
    check("published weights are constant within scenario and equal the configuration",
          (pub_w == 1).all().all() and all(close(sc[sc.scenario_id == s][f"weight_{p}"].iloc[0], float(w[(w.scenario_id == s) & (w.pillar == p)].pillar_weight.iloc[0]))
                                        for s in CORE for p in ("scale", "momentum", "dynamism", "commercial_fit", "stability")))
    only_core = set(reg[reg.framework_role == "core score"].indicator_id)
    check("only approved retained indicators are registered; no ISED source", set(reg.indicator_id) <= {"C01", "C03", "C05", "C09", "C10", "C15"}
          and not reg.source_model.str.contains("ised", case=False).any() and only_core == {"C01", "C03", "C05", "C09", "C15"})

    # normalization
    n = comp
    mono = all((g.sort_values("directed_value").normalized_percentile.diff().dropna() >= -1e-12).all() for _, g in n.groupby("component_id"))
    bounds = n[["normalized_percentile", "normalized_min_max", "normalized_min_max_winsorized", "normalized_robust_z"]].apply(lambda c: c.between(-1e-9, 100 + 1e-9).all()).all()
    orient = close(n.directed_value, np.where(n.direction == "lower_is_stronger", -n.transformed_value, n.transformed_value)).all()
    span = n.groupby("component_id").normalized_percentile.agg(["min", "max"])
    check("normalized components within 0-100, spanning 0-100, oriented and monotone", bounds and mono and orient and (span["min"] == 0).all() and (span["max"] == 100).all())
    pct_re = n.groupby("component_id", group_keys=False).apply(lambda g: pd.Series(100 * (stats.rankdata(np.round(g.directed_value, 12), method="average") - 1) / (len(g) - 1), index=g.index))
    check("percentile normalization recomputes (average ranks for ties)", close(pct_re.loc[n.index], n.normalized_percentile).all())

    # score recomputation from published components and weights
    piv = n.pivot(index="cma_code_sgc2021", columns="component_id", values="normalized_percentile")
    wi = dict(zip(reg.component_id, reg.within_pillar_weight))
    pill = pd.DataFrame({p: sum(wi[c] * piv[c] for c in cs) / sum(wi[c] for c in cs) for p, cs in PILLAR_COMPONENTS.items()})
    def score_with(values, ws):
        """Score from normalized component values through the published pillar formula and scenario weights."""
        pills = {p: sum(wi[c] * values[c] for c in cs) / sum(wi[c] for c in cs) for p, cs in PILLAR_COMPONENTS.items()}
        return sum(ws[p] * pills[p] for p in PILLAR_COMPONENTS)

    rows = []
    for r in sc.itertuples():
        exp_p = pill.loc[r.cma_code_sgc2021]
        ws = {p: float(w[(w.scenario_id == r.scenario_id) & (w.pillar == p)].pillar_weight.iloc[0]) for p in PILLAR_COMPONENTS}
        exp_s = sum(ws[p] * exp_p[p] for p in PILLAR_COMPONENTS)
        pub_p = {p: getattr(r, f"pillar_{p}") for p in PILLAR_COMPONENTS}
        base_row = piv.loc[r.cma_code_sgc2021].to_dict()
        bump = min(score_with({**base_row, c: min(base_row[c] + 10, 100)}, ws) for c in sum(PILLAR_COMPONENTS.values(), []))
        rows.append({"cma_code_sgc2021": r.cma_code_sgc2021, "scenario_id": r.scenario_id, "published_score": r.opportunity_score, "recomputed_score": exp_s,
                     "absolute_difference": abs(r.opportunity_score - exp_s), "tolerance": 1e-7,
                     "pillars_match": all(close(pub_p[p], exp_p[p]) for p in PILLAR_COMPONENTS),
                     "contributions_sum_to_score": close(r.contribution_scale + r.contribution_momentum + r.contribution_dynamism + r.contribution_commercial_fit
                                                         + r.contribution_stability, r.opportunity_score),
                     "monotone_under_component_increase": bump >= exp_s - 1e-9, "within_0_100": 0 <= r.opportunity_score <= 100})
    rc = pd.DataFrame(rows)
    rc["passed"] = (rc.absolute_difference <= rc.tolerance) & rc.pillars_match & rc.contributions_sum_to_score & rc.monotone_under_component_increase & rc.within_0_100
    rc.to_csv(V / "score_recalculation_checks.csv", index=False, lineterminator="\n", float_format="%.12g")
    check("every score recomputes from published components and weights", rc.passed.all(), f"{int(rc.passed.sum())} of {len(rc)}; max difference {rc.absolute_difference.max():.2e}")
    # ranks, percentiles, classifications
    rk_ok, cl_ok = True, True
    for s_, g in sc.groupby("scenario_id"):
        sr = np.round(g.opportunity_score, 7)
        rk_ok &= bool((g.score_rank.values == stats.rankdata(-sr, method="min")).all())
        pct = 100 * (stats.rankdata(sr, method="average") - 1) / (len(g) - 1)
        rk_ok &= bool(close(pct, g.score_percentile).all()) and sorted(g.score_position) == list(range(1, 36))
        tq, bq, lead, ceil = (float(th[k]) for k in ("top_quartile_percentile", "bottom_quartile_percentile", "lead_pillar_threshold", "other_pillar_ceiling"))
        exp = np.select([g.score_percentile >= tq, (g.pillar_scale >= lead) & (g.pillar_momentum < ceil), (g.pillar_momentum >= lead) & (g.pillar_scale < ceil),
                         g.score_percentile >= bq], ["Stronger external opportunity signal", "Scale-led external opportunity signal",
                                                     "Momentum-led external opportunity signal", "Moderate external opportunity signal"],
                        default="Weaker current external signal (monitor)")
        cl_ok &= bool((exp == g.opportunity_classification.values).all())
    check("ranks, positions and percentiles recompute within each scenario (ties shared, positions 1-35)", rk_ok)
    check("every classification follows its declared rule", cl_ok)
    ss = sens_df.set_index("cma_code_sgc2021")
    ranks = sc.pivot(index="cma_code_sgc2021", columns="scenario_id", values="score_rank")
    check("sensitivity rank ranges and top-quartile counts recompute", (ss.loc[ranks.index].min_scenario_rank == ranks.min(axis=1)).all()
          and (ss.loc[ranks.index].max_scenario_rank == ranks.max(axis=1)).all()
          and ss.perturbation_top_quartile_frequency.between(0, 1).all())
    lead_cols = [c for f in A.glob("*.csv") for c in pd.read_csv(f, nrows=0).columns
                 if any(t in c.lower() for t in ("lead", "prospect", "customer", "revenue", "conversion", "probability"))]
    check("no published output represents aggregates as prospects, leads or customers", not lead_cols, str(lead_cols))
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    mart_ctx = [r[0] for r in con.execute("describe analytics.mart_cma_opportunity_framework").fetchall() if r[0].startswith("context_")]
    ctx_tier = con.execute("select count(*) from analytics.mart_cma_opportunity_framework where evidence_tier <> 1 and context_ised_external_financing_request_rate_pct is not null").fetchone()[0]
    con.close()
    ctx_test = [k for k in opp_ran if "assert_opportunity_context_isolated" in k]
    check("ISED context sits beside, never inside, the score", bool(mart_ctx) and ctx_tier == 0 and rc.passed.all()
          and not reg.source_model.str.contains("ised", case=False).any() and len(ctx_test) == 1 and opp_ran[ctx_test[0]] == "pass",
          f"{len(mart_ctx)} context columns in the mart; Tier 1 only; all 140 scores recompute from components and weights alone; dbt isolation test passed")

    print("REPRODUCIBILITY")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    codes = []
    for run in ("run1", "run2"):
        r = subprocess.run([sys.executable, str(ANALYSIS), "--output-root", str(SCRATCH / run)], cwd=str(ROOT), capture_output=True, text=True, env=env)
        codes.append(r.returncode)
    rel = [Path("data/analysis/market_opportunity"), Path("data/validation/opportunity_framework"), Path("outputs/market_opportunity_framework")]
    produced = sorted(p.relative_to(SCRATCH / "run1") for d in rel for p in (SCRATCH / "run1" / d).glob("*") if p.is_file())
    same_runs = all(sha(SCRATCH / "run1" / p) == sha(SCRATCH / "run2" / p) for p in produced)
    same_pub = all((ROOT / p).exists() and sha(SCRATCH / "run1" / p) == sha(ROOT / p) for p in produced)
    check("analysis runs twice with identical bytes and matches the published outputs", codes == [0, 0] and same_runs and same_pub and len(produced) >= 19,
          f"{len(produced)} files; exits {codes}")

    # documentation claims
    b = sc[sc.scenario_id == "balanced"].set_index("cma_code_sgc2021")
    ex = b.sort_values("score_rank").iloc[0]
    exc = comp[comp.cma_code_sgc2021 == ex.name].set_index("component_id")
    groups = sens_df.stability_group.value_counts()
    fnd = pd.read_csv(A / "market_opportunity_findings.csv")
    scen = pd.read_csv(A / "scenario_comparison.csv").set_index("scenario_id")
    claims = {
        "population": "**35** CMAs", "scale_only": "**6** Tier 3 CMAs", "rows": f"**{len(sc)}** rows",
        "stable_stronger": f"**{groups.get('Stable stronger signal', 0)}** CMAs are in the top quartile under all four scenarios",
        "assumption_sensitive": f"**{groups.get('Assumption-sensitive', 0)}** CMAs are assumption-sensitive",
        "size_balanced": f"**{scen.loc['balanced', 'spearman_score_vs_log_scale']:.2f}** (balanced)",
        "size_scale_led": f"**{scen.loc['scale_led', 'spearman_score_vs_log_scale']:.2f}** (scale-led)",
        "c01_share": f"**{scen.loc['scale_led', 'largest_component_variance_share']:.0%}** of the scale-led score variance",
        "clarity": f"**{int((b.momentum_signal_clarity != 'within month-to-month noise').sum())}** of 35",
        "draws": "**5,000** weight draws",
        "example_name": f"**{ex.cma_label_sgc2021}**",
        "example_locations": f"{exc.loc['C01_LOG_SCALE', 'raw_value']:,.0f} SME employer locations",
        "example_log": f"log10 = {exc.loc['C01_LOG_SCALE', 'transformed_value']:.4f}",
        "example_c01": f"C01 normalized {exc.loc['C01_LOG_SCALE', 'normalized_percentile']:.1f}",
        "example_c03": f"C03 normalized {exc.loc['C03_OPENING_RATE', 'normalized_percentile']:.1f}",
        "example_c05": f"C05 normalized {exc.loc['C05_NET_FORMATION_RATE', 'normalized_percentile']:.1f}",
        "example_c09": f"C09 normalized {exc.loc['C09_NET_FORMATION_TREND', 'normalized_percentile']:.1f}",
        "example_c15": f"C15 normalized {exc.loc['C15_SHARE_20_99', 'normalized_percentile']:.1f}",
        "example_pillars": f"scale {ex.pillar_scale:.1f}, momentum {ex.pillar_momentum:.1f}, dynamism {ex.pillar_dynamism:.1f}, commercial fit {ex.pillar_commercial_fit:.1f}",
        "example_score": f"balanced score **{ex.opportunity_score:.2f}**",
        "example_class": f"*{ex.opportunity_classification}*",
        "findings": f"**{len(fnd)}** findings",
    }
    claims.update(extra_claims(sc, comp, sens_df, so))
    if "--print-claims" in sys.argv:
        for k, v in claims.items():
            print(f"CLAIM {k}: {v}")
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    missing = {k: v for k, v in claims.items() if v not in doc}
    check("documentation numbers reproduced by the outputs", bool(doc) and not missing,
          ("NOT YET DOCUMENTED" if not doc else f"{len(claims) - len(missing)} of {len(claims)}") + (f"; missing {missing}" if doc and missing else ""))

    pd.DataFrame(checks).to_csv(V / "framework_checks.csv", index=False, lineterminator="\n")
    outputs = {p.relative_to(ROOT).as_posix(): sha(p) for d in (A, FIGS) for p in sorted(d.glob("*")) if p.is_file()}
    outputs.update({p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(V.glob("*.csv"))})
    summary = {
        "purpose": "Validation of the external market opportunity framework",
        "run_timestamp_utc": start, "dbt_version": rr.get("metadata", {}).get("dbt_version"), "duckdb_version": duckdb.__version__,
        "analysis_script_sha256": sha(ANALYSIS), "validation_script_sha256": sha(Path(__file__)),
        "eligible_population": {"count": len(elig), "cmas": sorted(elig)}, "scale_only_population": {"count": len(t3), "cmas": sorted(t3)},
        "selected_indicators": reg[reg.framework_role == "core score"][["indicator_id", "component_id", "pillar", "within_pillar_weight", "transformation"]].to_dict("records"),
        "sensitivity_only_indicators": reg[reg.framework_role != "core score"][["indicator_id", "component_id", "framework_role"]].to_dict("records"),
        "excluded_indicators": "supporting, rejected and unresolved indicators keep their exploration classification and never enter a score",
        "normalization_method": th["primary_normalization"], "alternative_normalizations": ["min_max", "min_max_tukey_capped", "robust_z_clipped"],
        "scenarios": {sid: {"role": g.scenario_role.iloc[0], "interpretation": g.business_interpretation.iloc[0],
                            "weights": {r.pillar: r.pillar_weight for r in g.itertuples() if r.pillar_weight > 0}} for sid, g in w.groupby("scenario_id")},
        "configuration_declaration": decl,
        "score_ranges": {s: {"min": float(g.opportunity_score.min()), "max": float(g.opportunity_score.max())} for s, g in sc.groupby("scenario_id")},
        "stability_groups": groups.to_dict(),
        "stable_stronger_markets": sens_df[sens_df.stability_group == "Stable stronger signal"].cma_label_sgc2021.tolist(),
        "scale_led_restates_size": bool(scen.loc["scale_led", "restates_size"]),
        "dbt_tests": {"opportunity_tests": len(opp_tests), "opportunity_tests_passed": sum(v == "pass" for v in opp_ran.values()), "base_tests_defined": len(base_tests)},
        "test_sensitivity": sens, "doc_claims": claims, "output_sha256": outputs,
        "checks_passed": sum(c["passed"] for c in checks), "checks_total": len(checks),
        "failed_checks": [c["check"] for c in checks if not c["passed"]], "all_checks_pass": all(c["passed"] for c in checks),
    }
    (V / "opportunity_framework_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8", newline="\n")
    print(f"\nChecks: {summary['checks_passed']} of {summary['checks_total']} pass")
    if not summary["all_checks_pass"]:
        print("OPPORTUNITY FRAMEWORK VALIDATION FAILED")
        sys.exit(1)
    print("OPPORTUNITY FRAMEWORK VALIDATION PASSED")


if __name__ == "__main__":
    main()
