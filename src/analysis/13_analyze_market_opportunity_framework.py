"""
Market opportunity framework: analysis, sensitivity and figures.

Reads the dbt marts that implement the framework (analytics.mart_cma_opportunity_framework,
analytics.mart_cma_opportunity_sensitivity, analytics.mart_cma_scale_only_context), the dbt normalization
and scenario models, and the reviewed configuration seeds, all from the DuckDB warehouse (read-only).
It does not re-implement the transformation or scoring logic, which lives in dbt. It adds:
- business-facing outputs (scores, components, sensitivity, classifications, scale-only context,
  scenario comparison, findings);
- a deterministic weight-perturbation analysis (fixed seed; published pillar scores re-weighted);
- normalization, transformation, double-counting and sensitivity evidence;
- diagnostic figures and a figure register.

The framework measures external market signals from public aggregates only. It is not a prospect or lead
list and does not account for bank market share, customers, branches, RM capacity, credit risk, acquisition
cost, conversion, revenue or profitability.

Outputs are built in temporary folders and published only after every control passes.
    C:/Users/abhij/anaconda3/python.exe src/analysis/13_analyze_market_opportunity_framework.py [--output-root DIR]
"""

from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import sys

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
EXPLORATION = ROOT / "data" / "analysis" / "market_exploration"
BASE_OUT = ROOT
if "--output-root" in sys.argv:
    BASE_OUT = Path(sys.argv[sys.argv.index("--output-root") + 1]).resolve()
OUT = BASE_OUT / "data" / "analysis" / "market_opportunity"
VAL = BASE_OUT / "data" / "validation" / "opportunity_framework"
FIG = BASE_OUT / "outputs" / "market_opportunity_framework"

CORE_SCENARIOS = ["balanced", "scale_led", "growth_led", "stability_aware"]
CORE_COMPONENTS = ["C01_LOG_SCALE", "C03_OPENING_RATE", "C05_NET_FORMATION_RATE", "C09_NET_FORMATION_TREND", "C15_SHARE_20_99"]
PILLARS = ["scale", "momentum", "dynamism", "commercial_fit"]
P_1097 = "1097 December 2025 (REF_DATE 2025-07)"
P_0722 = "0722 2025-06 to 2026-05 (12 months); 2023-06 to 2026-05 (36 months)"
PERIODS = f"{P_1097}; {P_0722}"
SCORE_UNIT = "0-100 relative index within the 35-CMA population"
ELIG = "full framework: 35 CMAs with complete 0722 momentum history"
LIMIT = "External public-aggregate signal only; excludes bank market share, customers, branches, RM capacity, credit risk, acquisition cost, conversion and profitability"
MARTS = "analytics.mart_cma_opportunity_framework"
TIER_COLORS = {1: "#1f5f8b", 2: "#e08a1e", 3: "#7f7f7f"}
CLASS_COLORS = {"Stronger external opportunity signal": "#1b7837", "Scale-led external opportunity signal": "#2166ac",
                "Momentum-led external opportunity signal": "#b35806", "Moderate external opportunity signal": "#8c8c8c",
                "Weaker current external signal (monitor)": "#d9d9d9"}
checks = []
READS = []          # every relation and file the analysis reads (gated)


def check(name, ok, detail=""):
    checks.append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def percentile_avg(values):
    """Percentile with average ranks for ties (same definition as the dbt macro), 0-100."""
    r = stats.rankdata(values, method="average")
    return 100.0 * (r - 1) / (len(values) - 1)


def load():
    con = duckdb.connect(str(WAREHOUSE), read_only=True)

    def q(sql):
        rel = sql.split(" from ")[1].split()[0]
        READS.append(f"warehouse:{rel}")
        return con.execute(sql).to_arrow_table().to_pandas()
    d = {
        "framework": q(f"select * from {MARTS} order by scenario_id, score_position"),
        "sensitivity": q("select * from analytics.mart_cma_opportunity_sensitivity order by balanced_rank, cma_code_sgc2021"),
        "scale_only": q("select * from analytics.mart_cma_scale_only_context order by cma_code_sgc2021"),
        "normalized": q("select * from intermediate.int_opportunity__normalized_indicators order by component_id, cma_code_sgc2021"),
        "scenario_scores": q("select * from intermediate.int_opportunity__scenario_scores order by scenario_id, normalization_method, score_position"),
        "registry": q("select * from reference.opportunity_indicator_registry order by component_id"),
        "weights": q("select * from reference.opportunity_weight_scenarios order by scenario_id, pillar"),
        "thresholds": q("select * from reference.opportunity_decision_thresholds order by parameter"),
        "dim_cma": q("select cma_code_sgc2021, cma_label_sgc2021, tier, province_code from analytics.dim_cma order by cma_code_sgc2021"),
    }
    con.close()
    def rd(name):
        READS.append(f"file:data/analysis/market_exploration/{name}")
        return pd.read_csv(EXPLORATION / name, dtype={"cma_code_sgc2021": str})
    mom = rd("cma_momentum_profile.csv")
    d["prior"] = mom[["cma_code_sgc2021", "mean_net_formation_rate_12m", "mean_net_formation_rate_prior_12m"]]
    d["exploration"] = mom.merge(rd("cma_market_scale_profile.csv")[["cma_code_sgc2021", "sme_employer_locations"]], on="cma_code_sgc2021").merge(
        rd("cma_size_profile.csv")[["cma_code_sgc2021", "share_20-99"]], on="cma_code_sgc2021")
    return d


def analyse(d):
    f, s, n = d["framework"], d["sensitivity"], d["normalized"]
    th = dict(zip(d["thresholds"].parameter, d["thresholds"].value))
    top_q, sens_thr = float(th["top_quartile_percentile"]), float(th["rank_sensitivity_threshold"])
    b = f[f.scenario_id == "balanced"].set_index("cma_code_sgc2021")
    out = {}

    # ---------- perturbation (published pillar scores, fixed seed) ----------
    rng = np.random.default_rng(int(th["perturbation_seed"]))
    draws = int(th["perturbation_draws"])
    lo, hi = float(th["perturbation_lower_bound"]), float(th["perturbation_upper_bound"])
    raw_w = rng.uniform(lo, hi, size=(draws, len(PILLARS)))
    w = raw_w / raw_w.sum(axis=1, keepdims=True)
    P = b[[f"pillar_{p}" for p in PILLARS]].to_numpy()
    scores = w @ P.T                                            # draws x 35
    pct = np.apply_along_axis(lambda x: percentile_avg(np.round(x, 9)), 1, scores)
    ranks = np.apply_along_axis(lambda x: stats.rankdata(-np.round(x, 9), method="min"), 1, scores)
    pert = pd.DataFrame({"cma_code_sgc2021": b.index, "perturbation_top_quartile_frequency": (pct >= top_q).mean(axis=0),
                         "perturbation_median_rank": np.median(ranks, axis=0), "perturbation_rank_p05": np.percentile(ranks, 5, axis=0),
                         "perturbation_rank_p95": np.percentile(ranks, 95, axis=0)})
    out["perturbation_meta"] = {"seed": int(th["perturbation_seed"]), "draws": draws, "bounds_before_renormalization": [lo, hi],
                                "renormalized_weight_min": float(w.min()), "renormalized_weight_max": float(w.max()),
                                "weights_sum_to_one": bool(np.allclose(w.sum(axis=1), 1.0)), "nonnegative": bool((w >= 0).all()),
                                "interpretation": "share of plausible weightings placing the market in the top quartile; an assumption-robustness measure, not statistical confidence"}

    # ---------- business outputs ----------
    meta = dict(source_models="dbt: mart_cma_opportunity_framework (from fact_cma_market_scale_snapshot, fact_cma_size_composition_snapshot, fact_cma_momentum_monthly)",
                period=PERIODS, statistical_unit=SCORE_UNIT, evidence_eligibility=ELIG, quality_limitation=LIMIT)
    score_cols = ["cma_code_sgc2021", "cma_label_sgc2021", "evidence_tier", "scenario_id", "scenario_name", "opportunity_score", "score_rank", "score_position",
                  "tied_markets", "score_percentile", "opportunity_classification", "dominant_pillar", "pillar_scale", "pillar_momentum", "pillar_dynamism",
                  "pillar_commercial_fit", "pillar_stability", "weight_scale", "weight_momentum", "weight_dynamism", "weight_commercial_fit", "weight_stability",
                  "contribution_scale", "contribution_momentum", "contribution_dynamism", "contribution_commercial_fit", "contribution_stability",
                  "momentum_signal_clarity", "momentum_e_flagged_observations", "boundary_change_flag", "normalization_method"]
    out["scores"] = f[score_cols].assign(**meta)
    comp = n.merge(d["registry"][["component_id", "component_name", "framework_role"]], on=["component_id", "framework_role"], how="left")
    comp = comp.merge(d["dim_cma"][["cma_code_sgc2021", "cma_label_sgc2021", "tier"]], on="cma_code_sgc2021")
    out["components"] = comp[["cma_code_sgc2021", "cma_label_sgc2021", "tier", "component_id", "indicator_id", "component_name", "pillar", "within_pillar_weight",
                              "direction", "framework_role", "raw_value", "transformation", "transformed_value", "directed_value", "normalized_percentile",
                              "normalized_min_max", "normalized_min_max_winsorized", "normalized_robust_z", "winsorization_applied", "lower_cap", "upper_cap",
                              "tie_count", "usable_observations", "required_observations", "e_flagged_observations", "input_status", "source_model",
                              "source_columns", "period", "statistical_unit"]].rename(columns={"tier": "evidence_tier", "statistical_unit": "raw_statistical_unit"}).assign(
        statistical_unit="raw: see raw_statistical_unit; normalized: 0-100 percentile within 35 CMAs", evidence_eligibility=ELIG, quality_limitation=LIMIT,
        source_models="dbt: int_opportunity__normalized_indicators")
    sens = s.merge(pert, on="cma_code_sgc2021")
    out["sensitivity"] = sens.assign(**{**meta, "statistical_unit": "rank (1 = strongest of 35) and 0-100 score"})
    wide = f.pivot(index="cma_code_sgc2021", columns="scenario_id", values="opportunity_classification").add_prefix("classification_")
    cls = s.set_index("cma_code_sgc2021")[["cma_label_sgc2021", "evidence_tier", "balanced_rank", "stability_group", "position_driver", "classification_consistency",
                                          "top_quartile_scenarios", "momentum_signal_clarity"]].join(wide).join(pert.set_index("cma_code_sgc2021")["perturbation_top_quartile_frequency"])
    out["classifications"] = cls.reset_index().assign(**meta)
    out["scale_only"] = d["scale_only"].assign(evidence_eligibility="scale-only: no 0722 momentum history; not scored or ranked",
                                               quality_limitation="Percentiles across 41 CMAs; not comparable with framework percentiles; no momentum evidence",
                                               source_models="dbt: mart_cma_scale_only_context", period=P_1097)

    # ---------- scenario comparison ----------
    logscale = b["c01_log10_sme_employer_locations"]
    comp_cols = {"scale": ["c01_normalized"], "momentum": ["c05_normalized", "c09_normalized"], "dynamism": ["c03_normalized"],
                 "commercial_fit": ["c15_normalized"], "stability": ["c10_stability_normalized"]}
    rows, var_rows = [], []
    for sc in CORE_SCENARIOS:
        x = f[f.scenario_id == sc].set_index("cma_code_sgc2021").loc[b.index]
        S = x.opportunity_score
        top_sc = set(x[x.score_percentile >= top_q].index)
        top_b = set(b[b.score_percentile >= top_q].index)
        top_scale = set(b[percentile_avg(logscale.values) >= top_q].index)
        rows.append({"scenario_id": sc, "scenario_name": x.scenario_name.iloc[0], "score_min": S.min(), "score_max": S.max(), "score_median": S.median(),
                     "spearman_rank_vs_balanced": stats.spearmanr(x.score_rank, b.score_rank).statistic,
                     "top_quartile_overlap_with_balanced": len(top_sc & top_b), "top_quartile_size": len(top_sc),
                     "spearman_score_vs_log_scale": stats.spearmanr(S, logscale).statistic,
                     "top_quartile_markets_also_top_quartile_by_scale": len(top_sc & top_scale),
                     "max_rank_move_vs_balanced": int((x.score_rank - b.score_rank).abs().max()),
                     "mean_abs_rank_move_vs_balanced": float((x.score_rank - b.score_rank).abs().mean()),
                     "weights": "; ".join(f"{p} {x[f'weight_{p}'].iloc[0]:.3f}" for p in comp_cols)})
        var_s = S.var(ddof=0)
        for p, cols in comp_cols.items():
            wp = x[f"weight_{p}"].iloc[0]
            for c in cols:
                within = 0.5 if p == "momentum" else 1.0
                contrib = wp * within * x[c]
                var_rows.append({"scenario_id": sc, "pillar": p, "component": c.replace("_normalized", ""), "effective_weight": wp * within,
                                 "variance_share": float(np.cov(contrib, S, ddof=0)[0, 1] / var_s) if var_s else np.nan})
    scen = pd.DataFrame(rows)
    vmax = pd.DataFrame(var_rows).sort_values("variance_share", ascending=False).groupby("scenario_id").head(1).set_index("scenario_id")
    scen["largest_component"] = scen.scenario_id.map(vmax.component)
    scen["largest_component_variance_share"] = scen.scenario_id.map(vmax.variance_share)
    scen["restates_size"] = scen.spearman_score_vs_log_scale >= float(th["size_restatement_spearman"])
    scen["single_component_dominates"] = scen.largest_component_variance_share >= float(th["component_dominance_share"])
    out["scenario_comparison"] = scen.assign(source_models=f"dbt: {MARTS}", period=PERIODS, statistical_unit="rank correlation, counts and 0-100 scores",
                                             evidence_eligibility=ELIG, quality_limitation=LIMIT)
    out["variance_shares"] = pd.DataFrame(var_rows)
    out["contributions"] = out["variance_shares"].merge(
        pd.DataFrame(rows)[["scenario_id", "scenario_name"]], on="scenario_id").assign(
        nominal_share_of_weight=lambda x: x.effective_weight, variance_share_minus_weight=lambda x: x.variance_share - x.effective_weight,
        source_models=f"dbt: {MARTS}", period=PERIODS, statistical_unit="share of score variance (covariance of weighted component with score / score variance)",
        evidence_eligibility=ELIG, quality_limitation=LIMIT)
    ex = d["exploration"].set_index("cma_code_sgc2021").loc[b.index]
    lin = []
    for comp_id, mart_col, ex_col in (("C01", "c01_sme_employer_locations", "sme_employer_locations"), ("C03", "c03_opening_rate_12m", "mean_opening_rate_12m"),
                                      ("C05", "c05_net_formation_rate_12m", "mean_net_formation_rate_12m"),
                                      ("C09", "c09_net_formation_trend_per_year", "trend_net_formation_rate_36m_per_year"),
                                      ("C10", "c10_net_formation_rate_sd_12m", "volatility_net_formation_rate_12m_sd"), ("C15", "c15_share_20_99", "share_20-99")):
        diff = (b[mart_col] - ex[ex_col]).abs()
        lin.append({"indicator_id": comp_id, "framework_column": mart_col, "exploration_column": ex_col, "cmas_compared": len(diff),
                    "max_absolute_difference": float(diff.max()), "max_relative_difference": float((diff / ex[ex_col].abs()).max()),
                    "tolerance_absolute": 1e-10, "passed": bool(diff.max() <= 1e-10)})
    out["lineage"] = pd.DataFrame(lin).assign(source_models=f"dbt: {MARTS}; validated market-exploration profiles", period=PERIODS,
                                              statistical_unit="indicator raw units", evidence_eligibility=ELIG)

    # ---------- normalization comparison ----------
    ss = d["scenario_scores"]
    bal = ss[ss.scenario_id == "balanced"]
    prim = bal[bal.normalization_method == th["primary_normalization"]].set_index("cma_code_sgc2021")
    nrows = []
    for m, g in bal.groupby("normalization_method"):
        g = g.set_index("cma_code_sgc2021").loc[prim.index]
        shift = (g.score_rank - prim.score_rank).abs()
        nrows.append({"normalization_method": m, "is_primary": m == th["primary_normalization"],
                      "spearman_balanced_rank_vs_primary": stats.spearmanr(g.score_rank, prim.score_rank).statistic,
                      "max_rank_shift_vs_primary": int(shift.max()), "markets_shifting_9_or_more": int((shift >= sens_thr).sum()),
                      "top_quartile_overlap_with_primary": len(set(g[g.score_percentile >= top_q].index) & set(prim[prim.score_percentile >= top_q].index)),
                      "spearman_score_vs_log_scale": stats.spearmanr(g.opportunity_score, logscale.loc[g.index]).statistic,
                      "bounded_0_100": bool(g.opportunity_score.between(0, 100).all())})
    for m in ("normalized_percentile", "normalized_min_max", "normalized_min_max_winsorized", "normalized_robust_z"):
        for c in CORE_COMPONENTS:
            v = n[n.component_id == c][m]
            nrows.append({"normalization_method": m, "component_id": c, "component_min": v.min(), "component_max": v.max(), "component_median": v.median(),
                          "component_mean": v.mean(), "share_of_markets_above_90": float((v > 90).mean())})
    out["normalization_comparison"] = pd.DataFrame(nrows).assign(source_models="dbt: int_opportunity__scenario_scores; int_opportunity__normalized_indicators",
                                                                period=PERIODS, statistical_unit="ranks and 0-100 normalized values", evidence_eligibility=ELIG)

    # ---------- transformation comparison ----------
    trows = []
    for c, g in n.groupby("component_id"):
        raw, tr = g.raw_value.astype(float), g.transformed_value.astype(float)
        trows.append({"component_id": c, "transformation": g.transformation.iloc[0], "raw_skewness": stats.skew(raw, bias=False),
                      "transformed_skewness": stats.skew(tr, bias=False),
                      "raw_max_to_median": float(raw.max() / raw.median()) if raw.median() else np.nan,
                      "transformed_range": float(tr.max() - tr.min()),
                      "markets_capped_by_tukey_fences": int(g.winsorization_applied.sum()),
                      "lower_cap_oriented": float(g.lower_cap.iloc[0]), "upper_cap_oriented": float(g.upper_cap.iloc[0]),
                      "spearman_raw_vs_transformed": stats.spearmanr(raw, tr).statistic,
                      "percentile_unchanged_by_transformation": bool(stats.spearmanr(raw, tr).statistic > 0.999999) if c != "C10_VOLATILITY_SCALE_ADJUSTED" else False,
                      "note": ("log10 halves the skew; percentile ranks are unchanged by any monotone transformation" if c == "C01_LOG_SCALE" else
                               "residual removes the scale relationship; ranks differ from raw volatility" if c == "C10_VOLATILITY_SCALE_ADJUSTED" else
                               "identity; extremes affect min-max only (capping tested)")})
    raw_scale = n[n.component_id == "C01_LOG_SCALE"].raw_value
    trows.append({"component_id": "C01_raw_count_min_max (not used)", "transformation": "none (raw count, min-max)",
                  "raw_skewness": stats.skew(raw_scale, bias=False), "note": f"raw-count min-max would give the median CMA {100 * (raw_scale.median() - raw_scale.min()) / (raw_scale.max() - raw_scale.min()):.1f} of 100"})
    out["transformation_comparison"] = pd.DataFrame(trows).assign(source_models="dbt: int_opportunity__normalized_indicators", period=PERIODS,
                                                                  statistical_unit="skewness, ranks and counts", evidence_eligibility=ELIG)

    # ---------- double counting ----------
    vs = out["variance_shares"]
    prior = d["prior"].set_index("cma_code_sgc2021").loc[b.index]
    raw_vol = b.c10_net_formation_rate_sd_12m
    small = logscale <= logscale.quantile(0.25)
    shift_vol = (s.set_index("cma_code_sgc2021").rank_shift_unadjusted_vs_adjusted_volatility).loc[b.index]
    size_thr, dom_thr = float(th["size_restatement_spearman"]), float(th["component_dominance_share"])
    sc_tab = out["scenario_comparison"].set_index("scenario_id")
    dc = [
        ("large markets dominate despite log transformation", "Spearman(score, log scale) per core scenario",
         {k: round(v, 3) for k, v in sc_tab.spearman_score_vs_log_scale.items()}, f"< {size_thr} in every scenario",
         bool((sc_tab.spearman_score_vs_log_scale < size_thr).all())),
        ("scenario ordering restates market size", "top-quartile markets that are also top-quartile by scale (of 9)",
         {k: int(v) for k, v in sc_tab.top_quartile_markets_also_top_quartile_by_scale.items()}, "reported", True),
        ("volatility penalises small markets twice", "Spearman(raw volatility, log scale) vs Spearman(scale-adjusted residual, log scale)",
         {"raw": round(stats.spearmanr(raw_vol, logscale).statistic, 3), "scale_adjusted": round(stats.spearmanr(b.c10_scale_adjusted_residual, logscale).statistic, 3)},
         "raw strongly negative; adjusted near 0", bool(stats.spearmanr(b.c10_scale_adjusted_residual, logscale).statistic ** 2 < 0.05)),
        ("small markets lose rank under the unadjusted penalty", "mean rank shift (unadjusted minus adjusted penalty), smallest-quartile vs other markets",
         {"smallest_quartile": round(float(shift_vol[small].mean()), 2), "others": round(float(shift_vol[~small].mean()), 2)}, "reported", True),
        ("C05 and C09 duplicate the momentum signal", "Spearman(C05, C09) and effective weight of each in balanced",
         {"spearman": round(stats.spearmanr(b.c05_net_formation_rate_12m, b.c09_net_formation_trend_per_year).statistic, 3),
          "effective_weight_each": 0.125, "momentum_pillar_weight": 0.25}, "averaged within the momentum pillar (no extra pillar weight)", True),
        ("recent net formation persists between windows", "Spearman(C05 recent 12 months, prior 12 months)",
         round(stats.spearmanr(prior.mean_net_formation_rate_12m, prior.mean_net_formation_rate_prior_12m).statistic, 3), "reported", True),
        ("momentum direction is distinguishable from noise", "CMAs whose 12-month mean exceeds 2 standard errors",
         int((b.momentum_signal_clarity != "within month-to-month noise").sum()), "reported (heuristic ignores autocorrelation)", True),
        ("a pillar gains influence from its component count", "variance share of the momentum pillar vs its nominal weight (balanced)",
         {"variance_share": round(float(vs[(vs.scenario_id == "balanced") & (vs.pillar == "momentum")].variance_share.sum()), 3), "nominal_weight": 0.25},
         "pillar averaged; share reported", True),
        ("commercial fit favours a narrow type of market", "Spearman(C15, log scale), Spearman(C15, C03)",
         {"vs_scale": round(stats.spearmanr(b.c15_share_20_99, logscale).statistic, 3), "vs_dynamism": round(stats.spearmanr(b.c15_share_20_99, b.c03_opening_rate_12m).statistic, 3)},
         "reported", True),
        ("hidden scale influence through dynamism", "Spearman(C03, log scale)", round(stats.spearmanr(b.c03_opening_rate_12m, logscale).statistic, 3), "reported", True),
        ("a single component dominates a scenario", "largest component variance share per scenario",
         {sc: round(float(vs[vs.scenario_id == sc].variance_share.max()), 3) for sc in CORE_SCENARIOS}, f"< {dom_thr} in every scenario",
         bool(vs.groupby("scenario_id").variance_share.max().max() < dom_thr)),
    ]
    def status(c, r, ok):
        if isinstance(r, dict) and c in ("large markets dominate despite log transformation", "a single component dominates a scenario"):
            thr = size_thr if c.startswith("large") else dom_thr
            over = [k for k, v in r.items() if v >= thr]
            return "within declared threshold in all scenarios" if not over else f"exceeds declared threshold in: {', '.join(over)} (reported, not tuned)"
        return "within expectation" if ok else "reported"
    out["double_counting"] = pd.DataFrame([{"check": c, "measure": m, "result": json.dumps(r), "threshold_or_rule": t, "status": status(c, r, p)}
                                           for c, m, r, t, p in dc]).assign(
        source_models=f"dbt: {MARTS}; analytics.mart_cma_opportunity_sensitivity; market exploration prior-window means", period=PERIODS,
        statistical_unit="Spearman correlations, variance shares, rank shifts", evidence_eligibility=ELIG)

    # ---------- sensitivity validation ----------
    pr = []
    for i, a in enumerate(CORE_SCENARIOS):
        for c in CORE_SCENARIOS[i + 1:]:
            xa = f[f.scenario_id == a].set_index("cma_code_sgc2021").loc[b.index].score_rank
            xc = f[f.scenario_id == c].set_index("cma_code_sgc2021").loc[b.index].score_rank
            pr.append({"comparison": f"{a} vs {c}", "spearman_rank": stats.spearmanr(xa, xc).statistic, "max_abs_rank_shift": int((xa - xc).abs().max())})
    groups = s.stability_group.value_counts().to_dict()
    pr.append({"comparison": "stability groups (count of CMAs)", "detail": json.dumps(groups)})
    pr.append({"comparison": "weighting-sensitive CMAs", "detail": int(s.weighting_sensitive.sum())})
    pr.append({"comparison": "indicator-dependent CMAs (leave-one-out shift >= 9)", "detail": int(s.indicator_dependent.sum())})
    pr.append({"comparison": "normalization-sensitive CMAs (shift >= 9)", "detail": int(s.normalization_sensitive.sum())})
    pr.append({"comparison": "perturbation", "detail": json.dumps(out["perturbation_meta"])})
    loo_share = s.most_influential_component.value_counts().to_dict()
    pr.append({"comparison": "most influential left-out component (count of CMAs)", "detail": json.dumps(loo_share)})
    out["sensitivity_validation"] = pd.DataFrame(pr).assign(source_models="dbt: mart_cma_opportunity_sensitivity; mart_cma_opportunity_framework",
                                                            period=PERIODS, statistical_unit="ranks, correlations and counts", evidence_eligibility=ELIG)
    out["pert"] = pert
    return out


def decisions(d, out):
    f = d["framework"]; b = f[f.scenario_id == "balanced"].set_index("cma_code_sgc2021")
    reg = d["registry"].set_index("component_id")
    logscale = b.c01_log10_sme_employer_locations
    raw = {"C01": b.c01_sme_employer_locations, "C03": b.c03_opening_rate_12m, "C05": b.c05_net_formation_rate_12m,
           "C09": b.c09_net_formation_trend_per_year, "C10": b.c10_net_formation_rate_sd_12m, "C15": b.c15_share_20_99}
    rho = lambda a, c: round(stats.spearmanr(a, c).statistic, 2)  # noqa: E731
    notes = {
        "C01": ("Addressable market scale", "core score", "log10 then percentile rank", f"skew {stats.skew(raw['C01'], bias=False):.2f} raw vs {stats.skew(logscale, bias=False):.2f} log; top 3 CMAs hold 52.1% of 41-CMA locations",
                f"C03 {rho(logscale, raw['C03'])}; C15 {rho(logscale, raw['C15'])}; C10 {rho(logscale, raw['C10'])}", "Locations, not businesses; strongly concentrated"),
        "C03": ("Business dynamism (churn intensity)", "core score", "identity then percentile rank", f"range {raw['C03'].min():.4f}-{raw['C03'].max():.4f}",
                f"C01 {rho(raw['C03'], logscale)}; C15 {rho(raw['C03'], raw['C15'])}; closure rate 0.98 (exploration)", "Moves with closures: churn, not net growth"),
        "C05": ("Recent net-formation rate", "core score", "identity then percentile rank (within-pillar weight 0.5)",
                f"{int((b.momentum_signal_clarity != 'within month-to-month noise').sum())} of 35 CMAs clearly distinguishable from noise",
                f"C09 {rho(raw['C05'], raw['C09'])}; C03 {rho(raw['C05'], raw['C03'])}", "Small net rates; most CMA differences within month-to-month noise"),
        "C09": ("36-month net-formation trend", "core score", "OLS slope x12 then percentile rank (within-pillar weight 0.5)", f"range {raw['C09'].min():.5f}-{raw['C09'].max():.5f} per year",
                f"C05 {rho(raw['C09'], raw['C05'])}", "Linear trend; not a forecast"),
        "C10": ("Stability (volatility of net formation)", "sensitivity scenario only", "log10 SD, residual on log10 scale, negated, percentile rank",
                f"raw volatility vs log scale {rho(raw['C10'], logscale)}; residual vs log scale {rho(b.c10_scale_adjusted_residual, logscale)}",
                f"C01 {rho(raw['C10'], logscale)}", "Raw penalty would penalise small markets twice; used scale-adjusted in the stability-aware scenario (10%) and as the basis of the momentum signal-clarity confidence measure"),
        "C15": ("Commercial fit (20-99 share)", "core score", "identity then percentile rank", f"range {raw['C15'].min():.3f}-{raw['C15'].max():.3f}",
                f"C01 {rho(raw['C15'], logscale)}; C03 {rho(raw['C15'], raw['C03'])}", "Location-based size mix; highest in mid-sized Quebec, Northern Ontario and Atlantic markets"),
    }
    rows = []
    for ind, (name, role, norm, dist, red, lim) in notes.items():
        comps = reg[reg.indicator_id == ind]
        r0 = comps.iloc[0]
        rows.append({"indicator_id": ind, "business_name": name, "components": ";".join(comps.index), "business_purpose": r0.business_purpose,
                     "source_mart": r0.source_model, "source_columns": r0.source_columns, "calculation": "; ".join(comps.transformation),
                     "statistical_unit": r0.statistical_unit, "time_period": r0.period, "geographic_coverage": "35 full-framework CMAs (C01 and C15 also 41-CMA context)",
                     "raw_distribution": dist, "missingness": "0 of 35 (complete inputs tested)", "outliers": "retained; percentile ranks are unaffected; Tukey capping tested for min-max",
                     "direction_of_preference": "; ".join(comps.direction), "transformation": "; ".join(comps.transformation), "normalization_method": norm,
                     "pillar": "; ".join(comps.pillar), "redundancy": red, "quality_limitations": lim, "final_role": role,
                     "secondary_use": "confidence measure (momentum signal clarity)" if ind == "C10" else "",
                     "decision_basis": "declared before scoring from established exploration evidence; confirmed by double-counting checks"})
    others = [("C02", "rejected", "near-constant"), ("C14", "rejected", "duplicate of C13/C15"), ("C25", "rejected", "F/E-dominated at CMA level"),
              ("C27", "rejected", "8 CMAs only"), ("C08", "unresolved", "no change exceeds noise"), ("C13", "unresolved", "direction unresolved; mirrors C15"),
              ("C21", "unresolved", "22 CMAs only")] + [(c, "supporting context", "context only") for c in
                                                        ("C04", "C06", "C07", "C11", "C12", "C16", "C17", "C18", "C19", "C20", "C22", "C23", "C24", "C26", "C28")]
    for ind, status, why in others:
        rows.append({"indicator_id": ind, "business_name": "", "final_role": "supporting context" if status == "supporting context" else "excluded (classification unchanged)",
                     "decision_basis": f"exploration classification '{status}' kept ({why}); not reconsidered; never enters a score"})
    return pd.DataFrame(rows).assign(source_models="reference.opportunity_indicator_registry; dbt opportunity marts", period=PERIODS,
                                     statistical_unit="indicator decision", evidence_eligibility=ELIG)


def findings(d, out):
    f, s = d["framework"], d["sensitivity"]
    b = f[f.scenario_id == "balanced"].set_index("cma_code_sgc2021")
    sens = out["sensitivity"].set_index("cma_code_sgc2021")
    lab = b.cma_label_sgc2021
    sc = out["scenario_comparison"].set_index("scenario_id")
    dc = out["double_counting"].set_index("check")
    F = []

    def add(q, stmt, measure, value, comp, label, impl, pop=ELIG):
        F.append({"finding_id": f"O{len(F) + 1:02d}", "business_question": q, "finding_statement": stmt, "evidence_measure": measure,
                  "source_models": f"dbt: {MARTS}; analytics.mart_cma_opportunity_sensitivity", "statistical_unit": SCORE_UNIT, "period": PERIODS,
                  "population": pop, "evidence_eligibility": pop, "supporting_value": value, "comparison_value": comp, "quality_status": "public aggregates; momentum all-employer context",
                  "limitation": LIMIT, "evidence_label": label, "implication": impl})
    stable = s[s.stability_group == "Stable stronger signal"]
    add("Which markets show consistently stronger external signals?",
        f"{len(stable)} CMAs are in the top quartile under all four scenarios: {', '.join(stable.cma_label_sgc2021)}.",
        "top-quartile scenarios = 4", len(stable), 35, "FACT: DATA", "Candidates for deeper acquisition planning once internal data is added")
    pert_hi = sens[sens.perturbation_top_quartile_frequency >= 0.8]
    add("Which markets show consistently stronger external signals?",
        f"{len(pert_hi)} CMAs are in the top quartile in at least 80% of {out['perturbation_meta']['draws']:,} perturbed weightings: {', '.join(lab[pert_hi.index])}.",
        "perturbation top-quartile frequency >= 0.8", len(pert_hi), out["perturbation_meta"]["draws"], "FACT: DATA", "Confirms the scenario result under random plausible weights")
    for drv, q in (("scale-driven", "Which markets are primarily scale-led?"), ("momentum-driven", "Which markets are primarily momentum-led?"),
                   ("mixed scale and momentum", "Which markets are neither scale- nor momentum-driven?")):
        g = s[s.position_driver == drv]
        text = drv if drv != "mixed scale and momentum" else "neither scale- nor momentum-driven (scale and momentum pillars within 25 points)"
        add(q, f"{len(g)} CMAs are {text}: {', '.join(g.cma_label_sgc2021)}.", "position_driver (scale pillar minus momentum pillar, gap 25)", len(g), 35,
            "FACT: DATA", "Frame the acquisition rationale by driver: coverage and volume versus growth")
    allp = b[(b[["pillar_scale", "pillar_momentum", "pillar_dynamism", "pillar_commercial_fit"]] >= 50).all(axis=1)].sort_values("score_rank")
    add("Which markets are balanced across pillars?",
        f"{len(allp)} CMAs score at least 50 on all four core pillars (descriptive rule; not a classification): {', '.join(allp.cma_label_sgc2021)}.",
        "all four core pillar scores >= 50 (balanced scenario pillars)", len(allp), 35, "FACT: DATA",
        "Broad-based external signals; their placement depends least on any single pillar")
    sensi = s[s.stability_group == "Assumption-sensitive"]
    add("Which markets are sensitive to weighting assumptions?", f"{len(sensi)} CMAs are assumption-sensitive (rank range of 9 or more, or top quartile in some but not all scenarios): {', '.join(sensi.cma_label_sgc2021)}.",
        "stability_group", len(sensi), 35, "FACT: DATA", "Their placement depends on business priorities; decide the priority before comparing them")
    add("How much do rankings change across scenarios?", f"The median CMA's rank range across the four scenarios is {s.rank_range.median():.0f}; the largest is {int(s.rank_range.max())} ({s.loc[s.rank_range.idxmax(), 'cma_label_sgc2021']}); scenario rank correlations with balanced range from {sc.spearman_rank_vs_balanced.drop('balanced').min():.2f} to {sc.spearman_rank_vs_balanced.drop('balanced').max():.2f}.",
        "rank_range; Spearman vs balanced", float(s.rank_range.median()), int(s.rank_range.max()), "FACT: DATA", "Top and bottom are stable; the middle reorders")
    over = sc[sc.restates_size].index.tolist()
    under = [f"{k.replace('_', '-')} {v:.2f}" for k, v in sc.spearman_score_vs_log_scale.items() if k not in over]
    add("Is the framework dominated by market size?",
        f"Score and log scale correlate at {', '.join(under)} (Spearman), below the declared 0.8 threshold; the scale-led scenario reaches "
        f"{sc.loc['scale_led', 'spearman_score_vs_log_scale']:.2f} and C01 explains {sc.loc['scale_led', 'largest_component_variance_share']:.0%} of its score variance, "
        "so the scale-led scenario is essentially a size ordering.",
        "Spearman(score, log scale); component variance share", round(float(sc.loc['balanced', 'spearman_score_vs_log_scale']), 3), 0.8, "FACT: DATA",
        "Balanced, growth-led and stability-aware scores are not restatements of size; read the scale-led scenario as a size view")
    vr = json.loads(dc.loc["volatility penalises small markets twice", "result"])
    sm = json.loads(dc.loc["small markets lose rank under the unadjusted penalty", "result"])
    add("Does volatility add useful information?", f"Raw volatility is strongly related to size (Spearman {vr['raw']}); after scale adjustment it is not ({vr['scale_adjusted']}). An unadjusted penalty would move the smallest-quartile markets by {sm['smallest_quartile']:+.2f} ranks on average versus {sm['others']:+.2f} for others; stability-aware ranks differ little from balanced.",
        "volatility checks", vr["raw"], vr["scale_adjusted"], "INFERENCE", "Keep volatility as a confidence measure and a limited scale-adjusted component, not a core penalty")
    c15 = s.rank_shift_without_c15.abs()
    add("Does commercial fit materially change conclusions?", f"Removing commercial fit moves balanced ranks by {c15.median():.0f} places at the median and up to {int(c15.max())} ({s.loc[c15.idxmax(), 'cma_label_sgc2021']}); it counterweights scale and dynamism (Spearman with C03 {json.loads(dc.loc['commercial fit favours a narrow type of market', 'result'])['vs_dynamism']}).",
        "leave-one-out rank shift without C15", float(c15.median()), int(c15.max()), "FACT: DATA", "Commercial fit is material; its definition (20-99 locations) should be reviewed with sales strategy")
    vsm = json.loads(dc.loc["a pillar gains influence from its component count", "result"])
    add("Does any pillar gain hidden influence?", f"The momentum pillar explains {vsm['variance_share']:.0%} of balanced score variance against a 25% nominal weight. Its two components are averaged, so the excess comes from momentum being nearly independent of the other pillars, while dynamism and commercial fit partly offset each other (Spearman {json.loads(dc.loc['commercial fit favours a narrow type of market', 'result'])['vs_dynamism']}).",
        "variance share of the momentum pillar", vsm["variance_share"], 0.25, "INFERENCE", "Momentum differentiates markets more than its weight suggests; pair momentum-led readings with the signal-clarity measure")
    clar = int((b.momentum_signal_clarity != "within month-to-month noise").sum())
    ml = f[(f.opportunity_classification == "Momentum-led external opportunity signal") & (f.momentum_signal_clarity == "within month-to-month noise")]
    add("How reliable is the momentum signal?", f"Only {clar} of 35 CMAs have a 12-month mean net-formation rate clearly distinguishable from month-to-month noise; {len(ml)} of {int((f.opportunity_classification == 'Momentum-led external opportunity signal').sum())} momentum-led classifications (across scenarios) rest on an unclear 12-month signal.",
        "momentum_signal_clarity", clar, 35, "FACT: DATA", "Treat momentum-led placements as provisional; pair with the 36-month trend and internal data")
    so = d["scale_only"]
    big = so.sort_values("scale_percentile_41_cma", ascending=False)
    add("Which Tier 3 markets deserve separate qualitative review?", f"The six scale-only CMAs rank between the {so.scale_percentile_41_cma.min():.1f}th and {so.scale_percentile_41_cma.max():.1f}th 41-CMA scale percentile; the largest are {', '.join(big.cma_label_sgc2021.head(2))}. They have no momentum evidence and are not scored.",
        "scale_percentile_41_cma", float(so.scale_percentile_41_cma.max()), float(so.scale_percentile_41_cma.min()), "DECISION",
        "Review qualitatively (for example with local market knowledge) rather than by score", pop="scale-only: 6 Tier 3 CMAs")
    add("What internal data is needed before final acquisition-investment decisions?", "The score covers external evidence only. A decision needs current market share and customers, branch coverage, relationship-manager capacity, credit risk appetite, acquisition cost, conversion history and customer profitability.",
        "scope", "", "", "DECISION", "Next: combine with (synthetic, later) internal evidence; the external score alone does not set investment")
    return pd.DataFrame(F)


def figures(d, out):
    f, s, so = d["framework"], d["sensitivity"], d["scale_only"]
    b = f[f.scenario_id == "balanced"].set_index("cma_code_sgc2021")
    specs = []
    note = "Source: StatCan 33-10-1097 and 33-10-0722 via dbt marts. External public-aggregate signal; not leads or an investment decision."

    def fig1():
        fig, (ax, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [6, 1]})
        for c, g in b.groupby("opportunity_classification"):
            ax.scatter(g.pillar_scale, g.pillar_momentum, s=45, color=CLASS_COLORS[c], edgecolor="black", linewidth=0.4, label=c)
        for i, r in b[b.score_rank <= 9].iterrows():
            ax.annotate(r.cma_label_sgc2021, (r.pillar_scale, r.pillar_momentum), textcoords="offset points", xytext=(4, 3), fontsize=7)
        ax.axvline(50, color="grey", lw=0.6, ls="--"); ax.axhline(50, color="grey", lw=0.6, ls="--")
        ax.set_xlim(-3, 103); ax.set_ylim(-3, 103); ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
        ax2.scatter(so.scale_percentile_41_cma, np.zeros(len(so)), marker="|", s=250, color=TIER_COLORS[3])
        for _, r in so.iterrows():
            ax2.annotate(r.cma_label_sgc2021, (r.scale_percentile_41_cma, 0), textcoords="offset points", xytext=(0, 8), fontsize=6, ha="center", rotation=30)
        ax2.set_yticks([]); ax2.set_xlim(-3, 103)
        ax2.set_xlabel("Scale percentile across 41 CMAs (0-100), December 2025")
        ax2.set_title("Tier 3 scale-only CMAs (no momentum data; not scored)", fontsize=8)
        return fig, ax
    specs.append(("fig01_scale_momentum_opportunity_matrix", fig1, "Scale versus momentum, balanced scenario, 35 CMAs: December 2025 scale; 2025-06 to 2026-05 momentum",
                  "Scale pillar (0-100 percentile of log SME employer locations), December 2025",
                  "Momentum pillar (0-100; net formation 2025-06 to 2026-05 and 36-month trend)", "0-100 pillar score", "December 2025; 2025-06 to 2026-05",
                  "Which markets combine addressable scale with momentum?", "35 full-framework CMAs; 6 Tier 3 shown separately"))

    def fig2():
        order = b.sort_values("score_rank")
        cols = ["c01_normalized", "c05_normalized", "c09_normalized", "c03_normalized", "c15_normalized", "c10_stability_normalized"]
        fig, ax = plt.subplots(figsize=(8, 11))
        im = ax.imshow(order[cols].to_numpy(), cmap="viridis", vmin=0, vmax=100, aspect="auto")
        ax.set_xticks(range(len(cols)), ["C01 scale", "C05 net formation", "C09 36m trend", "C03 dynamism", "C15 commercial fit", "C10 stability*"], rotation=35, ha="right", fontsize=8)
        ax.set_yticks(range(len(order)), [f"{int(r)}. {l}" for r, l in zip(order.score_rank, order.cma_label_sgc2021)], fontsize=7)
        fig.colorbar(im, ax=ax, label="Normalized component (0-100 percentile within 35 CMAs)")
        return fig, ax
    specs.append(("fig02_component_heatmap", fig2, "Normalized opportunity components by CMA (ordered by balanced rank), 2025-06 to 2026-05 and December 2025",
                  "Framework component (*C10 scored only in the stability-aware scenario)", "CMA (balanced rank)", "0-100 percentile within 35 CMAs",
                  "2025-06 to 2026-05 and December 2025", "What drives each market's score?", "35 full-framework CMAs"))

    def fig3():
        order = b.sort_values("score_rank")
        fig, ax = plt.subplots(figsize=(9, 11))
        y = np.arange(len(order))
        sub = s.set_index("cma_code_sgc2021").loc[order.index]
        ax.hlines(y, sub.min_scenario_rank, sub.max_scenario_rank, color="#bdbdbd", lw=3)
        mk = {"balanced": "o", "scale_led": "s", "growth_led": "^", "stability_aware": "D"}
        for sc_, m in mk.items():
            x = f[f.scenario_id == sc_].set_index("cma_code_sgc2021").loc[order.index].score_rank
            ax.scatter(x, y, marker=m, s=22, label=sc_.replace("_", "-"), zorder=3)
        ax.set_yticks(y, order.cma_label_sgc2021, fontsize=7); ax.invert_yaxis(); ax.invert_xaxis()
        ax.axvline(9.5, color="grey", lw=0.6, ls="--"); ax.legend(fontsize=7, loc="upper left")
        return fig, ax
    specs.append(("fig03_scenario_rank_comparison", fig3, "Rank under each weighting scenario (bar = range), 2025-06 to 2026-05 and December 2025",
                  "Scenario rank (1 = strongest of 35; dashed line = top quartile)", "CMA (ordered by balanced rank)", "rank (1 = strongest of 35)",
                  "2025-06 to 2026-05 and December 2025", "How much does each market move when priorities change?", "35 full-framework CMAs"))

    def fig4():
        order = b.sort_values("score_rank")
        fig, ax = plt.subplots(figsize=(9, 11))
        left = np.zeros(len(order))
        for p, col in zip(["scale", "momentum", "dynamism", "commercial_fit"], ["#2166ac", "#b35806", "#762a83", "#1b7837"]):
            v = order[f"contribution_{p}"].to_numpy()
            ax.barh(range(len(order)), v, left=left, color=col, label=p.replace("_", " "))
            left += v
        ax.set_yticks(range(len(order)), [f"{int(r)}. {l}" for r, l in zip(order.score_rank, order.cma_label_sgc2021)], fontsize=7)
        ax.invert_yaxis(); ax.set_xlim(0, 100); ax.legend(fontsize=7, loc="lower right")
        return fig, ax
    specs.append(("fig04_score_contributions", fig4, "Balanced opportunity score by pillar contribution, 2025-06 to 2026-05 and December 2025",
                  "Balanced opportunity score (0-100 points; weight x pillar score)", "CMA (balanced rank)", "0-100 points",
                  "2025-06 to 2026-05 and December 2025", "Which pillars make up each market's score?", "35 full-framework CMAs"))

    def fig5():
        p = out["pert"].merge(b[["cma_label_sgc2021", "score_rank"]], left_on="cma_code_sgc2021", right_index=True).sort_values(
            ["perturbation_top_quartile_frequency", "score_rank"], ascending=[False, True])
        fig, ax = plt.subplots(figsize=(9, 10))
        colors = [CLASS_COLORS["Stronger external opportunity signal"] if v >= 0.8 else "#fdae61" if v >= 0.2 else "#d9d9d9" for v in p.perturbation_top_quartile_frequency]
        ax.barh(range(len(p)), p.perturbation_top_quartile_frequency * 100, color=colors)
        ax.set_yticks(range(len(p)), p.cma_label_sgc2021, fontsize=7); ax.invert_yaxis(); ax.set_xlim(0, 100)
        ax.axvline(80, color="grey", lw=0.6, ls="--")
        return fig, ax
    specs.append(("fig05_top_quartile_frequency", fig5,
                  f"Share of {out['perturbation_meta']['draws']:,} perturbed weightings placing each CMA in the top quartile, 2025-06 to 2026-05 and December 2025",
                  "Top-quartile frequency (% of weight draws; seed 20260927; pillar weights 0.10-0.40, renormalized)", "CMA", "% of weight draws",
                  "2025-06 to 2026-05 and December 2025", "Is a market's strong placement robust to plausible weights?", "35 full-framework CMAs"))

    def fig6():
        fig, ax = plt.subplots(figsize=(10, 5.5))
        sc_ = ax.scatter(b.c01_sme_employer_locations, np.ones(len(b)), c=b.opportunity_score, cmap="viridis", vmin=0, vmax=100, s=45, edgecolor="black", linewidth=0.3)
        ax.scatter(so.sme_employer_locations, np.zeros(len(so)), facecolors="none", edgecolors=TIER_COLORS[3], s=60, linewidth=1.2)
        for _, r in so.iterrows():
            ax.annotate(r.cma_label_sgc2021, (r.sme_employer_locations, 0), textcoords="offset points", xytext=(0, -12), fontsize=6, ha="center")
        ax.set_xscale("log"); ax.set_yticks([0, 1], ["Tier 3 scale-only\n(no momentum; not scored)", "Full framework\n(35 CMAs, balanced score)"])
        ax.set_ylim(-0.6, 1.6)
        fig.colorbar(sc_, ax=ax, label="Balanced opportunity score (0-100)")
        return fig, ax
    specs.append(("fig06_full_score_versus_scale_only", fig6, "Evidence coverage: scored markets versus scale-only markets, December 2025",
                  "SME employer locations (count, log scale), December 2025", "Evidence population", "count, log scale", "December 2025",
                  "Which markets can be scored, and where do the scale-only markets sit?", "41 CMAs (35 scored, 6 scale-only)"))

    rendered, register = {}, []
    for fid, fn, title, xl, yl, unit, period, question, population in specs:
        pngs = []
        for _ in range(2):
            fig, ax = fn()
            fig.suptitle(title, fontsize=10)
            if fid not in ("fig01_scale_momentum_opportunity_matrix",):
                ax.set_xlabel(xl)
            else:
                ax.set_xlabel(xl)
            ax.set_ylabel(yl)
            fig.text(0.01, 0.005, note, fontsize=6.5, ha="left", va="bottom")
            fig.tight_layout(rect=(0, 0.03, 1, 0.96))
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, metadata={"Software": None})
            plt.close(fig)
            pngs.append(buf.getvalue())
        rendered[fid] = pngs[0]
        register.append({"figure_id": fid, "filename": f"outputs/market_opportunity_framework/{fid}.png", "title": title, "business_question": question,
                         "x_label": xl, "y_label": yl, "unit": unit, "period": period, "population": population,
                         "source": "dbt marts: mart_cma_opportunity_framework, mart_cma_opportunity_sensitivity, mart_cma_scale_only_context",
                         "limitations": LIMIT, "reproducible_render": hashlib.sha256(pngs[0]).hexdigest() == hashlib.sha256(pngs[1]).hexdigest()})
    return rendered, pd.DataFrame(register)


def main():
    d = load()
    out = analyse(d)
    reg = decisions(d, out)
    fnd = findings(d, out)
    figs, fig_reg = figures(d, out)
    analysis_files = {"component_contribution_analysis.csv": out["contributions"], "cma_opportunity_scores.csv": out["scores"], "cma_opportunity_components.csv": out["components"],
                      "cma_opportunity_sensitivity.csv": out["sensitivity"], "cma_opportunity_classifications.csv": out["classifications"],
                      "cma_scale_only_context.csv": out["scale_only"], "scenario_comparison.csv": out["scenario_comparison"],
                      "market_opportunity_findings.csv": fnd,
                      "figure_register.csv": fig_reg.drop(columns="reproducible_render").assign(statistical_unit=fig_reg.unit, evidence_eligibility=fig_reg.population)}
    validation_files = {"indicator_decision_register.csv": reg, "transformation_comparison.csv": out["transformation_comparison"],
                        "normalization_comparison.csv": out["normalization_comparison"], "double_counting_checks.csv": out["double_counting"],
                        "sensitivity_validation.csv": out["sensitivity_validation"], "indicator_lineage_check.csv": out["lineage"]}

    print("CONTROLS")
    f = d["framework"]
    allowed_files = {f"file:data/analysis/market_exploration/{n}" for n in ("cma_momentum_profile.csv", "cma_market_scale_profile.csv", "cma_size_profile.csv")}
    bad = [r for r in READS if not (r.startswith("warehouse:") and r.split(":", 1)[1].split(".")[0] in ("analytics", "intermediate", "reference")) and r not in allowed_files]
    check("reads only dbt relations in the warehouse and validated exploration profiles", bool(READS) and not bad
          and not any(x in r for r in READS for x in ("data/processed", "data/raw", "data/cache")),
          f"{sum(r.startswith('warehouse:') for r in READS)} warehouse relations, {sum(r.startswith('file:') for r in READS)} exploration files" + (f"; unexpected {bad}" if bad else ""))
    check("raw indicators trace to the validated exploration values", out["lineage"].passed.all(), f"max absolute difference {out['lineage'].max_absolute_difference.max():.1e}")
    check("framework population is 35 CMAs x 4 core scenarios", f.cma_code_sgc2021.nunique() == 35 and len(f) == 140 and set(f.scenario_id) == set(CORE_SCENARIOS))
    check("scale-only population is the 6 Tier 3 CMAs", len(d["scale_only"]) == 6 and set(d["scale_only"].cma_code_sgc2021) == set(d["dim_cma"][d["dim_cma"].tier == 3].cma_code_sgc2021)
          and not set(d["scale_only"].cma_code_sgc2021) & set(f.cma_code_sgc2021))
    for name, df in {**analysis_files, **validation_files}.items():
        cols = set(df.columns)
        ok = {"period", "statistical_unit", "evidence_eligibility"} <= cols and ("source_models" in cols or "source" in cols or name == "figure_register.csv")
        check(f"output carries source, period, unit and eligibility: {name}", ok)
    pm = out["perturbation_meta"]
    check("perturbation weights nonnegative, renormalized, fixed seed", pm["weights_sum_to_one"] and pm["nonnegative"] and pm["seed"] == 20260927, f"{pm['draws']} draws")
    check("figures render reproducibly and carry units and periods", fig_reg.reproducible_render.all() and len(fig_reg) == 6
          and all(all(p_.strip() in t for p_ in per.split(";")) for t, per in zip(fig_reg.title, fig_reg.period)))
    forbidden = [c for df in analysis_files.values() for c in df.columns if any(t in c.lower() for t in ("lead", "prospect", "customer", "revenue", "conversion", "probability"))]
    check("no output represents public aggregates as prospects or leads", not forbidden, str(forbidden) if forbidden else "")
    check("no scale-only market has a score or rank", not any(c for c in out["scale_only"].columns if "score" in c and c != "full_score_eligible" or "rank" in c))
    if not all(c["passed"] for c in checks):
        print("CONTROLS FAILED: outputs not published")
        sys.exit(1)

    tmp = BASE_OUT / f".market_opportunity_build_{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    for sub in ("a", "v", "f"):
        (tmp / sub).mkdir(parents=True)
    for name, df in analysis_files.items():
        df.to_csv(tmp / "a" / name, index=False, encoding="utf-8", lineterminator="\n", float_format="%.10g")
    for name, df in validation_files.items():
        df.to_csv(tmp / "v" / name, index=False, encoding="utf-8", lineterminator="\n", float_format="%.10g")
    for fid, png in figs.items():
        (tmp / "f" / f"{fid}.png").write_bytes(png)
    for sub, dest in (("a", OUT), ("v", VAL), ("f", FIG)):
        dest.mkdir(parents=True, exist_ok=True)
        for p in sorted((tmp / sub).iterdir()):
            os.replace(p, dest / p.name)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"Published {len(analysis_files)} analysis files, {len(validation_files)} evidence files and {len(figs)} figures; controls {sum(c['passed'] for c in checks)} of {len(checks)}")


if __name__ == "__main__":
    main()
