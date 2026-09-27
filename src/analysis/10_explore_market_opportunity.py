"""
Phase H — exploratory market analysis and candidate-indicator profiling for the
Canadian SME Acquisition Strategy.

Scope: describe what the validated Phase G analytical views say about market scale,
recent business momentum, industry and size composition, financing context and evidence
quality, and profile candidate indicators for a later (Phase I) opportunity framework.
It does NOT calculate a composite score, assign weights, rank CMAs, recommend markets,
create CRM data, define routing rules or build a dashboard.

Design:
- Reads its analytical inputs only from the dbt marts (schema `analytics` in the DuckDB warehouse
  data/warehouse/sme_banking.duckdb, opened read-only) plus validated summaries; every input is
  logged and gated. The run requires the dbt tests and the mart-to-baseline reconciliation
  (data/validation/dbt_migration/migration_summary.json) to have passed. The baseline Parquet
  views are hash-checked for integrity only. Raw files and caches are never opened (they are
  checked by size and modification time only).
- `--output-root <dir>` writes the outputs under <dir> instead of the project (used by the
  regression check before the protected outputs are replaced).
- Joins use codes. Every measure keeps its source view, statistical unit and period.
- Momentum = StatCan 0722 business-sector total (T004), all sizes: labelled
  "all-employer-business momentum context"; 35 CMAs only (Tier 3 has no 0722 history).
- 1097 measures stay location-based; ISED stays contextual (F excluded, E cautioned,
  '..' unavailable, no counts from percentages, no regional values on CMAs).
- Outputs are built in temporary folders, validated, then moved onto the exact targets.
  Any failed required gate exits with status 1.

Run (Anaconda interpreter; works from any working directory):
    C:/Users/abhij/anaconda3/python.exe src/analysis/10_explore_market_opportunity.py
"""

from pathlib import Path
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True

SCRIPT_VERSION = "phase-h/1.0"
DATA = ROOT / "data"
VIEWS = DATA / "processed" / "analytical_views"
VAL = DATA / "validation"
OUT = DATA / "analysis" / "market_exploration"
FIG = ROOT / "outputs" / "phase_h_market_exploration"
if "--output-root" in sys.argv:          # regression runs write to a scratch root; the default is the project itself
    _ALT = Path(sys.argv[sys.argv.index("--output-root") + 1]).resolve()
    OUT = _ALT / "data" / "analysis" / "market_exploration"
    FIG = _ALT / "outputs" / "phase_h_market_exploration"
WAREHOUSE = DATA / "warehouse" / "sme_banking.duckdb"
MIGRATION_SUMMARY = VAL / "dbt_migration" / "migration_summary.json"
MART_ORDER = {"dim_cma": "cma_code_sgc2021", "dim_industry": "industry_system, industry_code", "dim_size_band": "size_band_key",
              "fact_cma_market_scale_snapshot": "cma_code_sgc2021, size_scope, snapshot_period",
              "fact_cma_sector_composition_snapshot": "cma_code_sgc2021, naics_sector, snapshot_period",
              "fact_cma_size_composition_snapshot": "cma_code_sgc2021, size_band_key, snapshot_period",
              "fact_ised_financing_context": "ised_table, measure_id, segment_dimension, segment_key",
              "fact_cma_momentum_monthly": "cma_code_sgc2016, ref_month, size_band_key, industry_code_0722, measure"}
DOC05 = ROOT / "docs" / "05_external_market_exploration.md"
DOC04 = ROOT / "docs" / "04_analytical_data_layer.md"
SUMMARIES = {"A": VAL / "33100722" / "phase_a_summary.json", "B": VAL / "33101097" / "phase_b_summary.json",
             "C": VAL / "ised_2023" / "phase_c_summary.json", "D": VAL / "source_comparison" / "phase_d_summary.json",
             "E": VAL / "crosswalks" / "phase_e_summary.json", "F": VAL / "mvp_grain" / "phase_f_summary.json",
             "G": VAL / "analytical_views" / "phase_g_summary.json"}
HASHED_PROTECTED = [DATA / "processed", DATA / "reference", VAL, DATA / "source_manifest.csv"]
STAT_PROTECTED = [DATA / "raw", DATA / "cache"]          # rule 7: never opened; size + mtime only

# Windows (hard-coded and gated)
RECENT = [str(p) for p in pd.period_range("2025-06", "2026-05", freq="M")]
PRIOR = [str(p) for p in pd.period_range("2024-06", "2025-05", freq="M")]
W36 = [str(p) for p in pd.period_range("2023-06", "2026-05", freq="M")]
PANDEMIC = [str(p) for p in pd.period_range("2020-03", "2021-12", freq="M")]
MIN_USABLE_12, MIN_USABLE_36 = 12, 30

MOM_LABEL = "all-employer-business momentum context"
TIER3_LABEL = "scale-only; not eligible for the primary momentum-based comparison"
CORE_MEASURES = ["active", "openings", "closures", "net_formation", "opening_rate", "closure_rate", "net_formation_rate"]
RATES = ["opening_rate", "closure_rate", "net_formation_rate"]
P_RECENT = "0722 monthly, seasonally adjusted; trailing 12 months 2025-06 to 2026-05"
P_PRIOR = "0722 monthly, seasonally adjusted; prior 12 months 2024-06 to 2025-05"
P_W36 = "0722 monthly, seasonally adjusted; trailing 36 months 2023-06 to 2026-05"
P_1097 = "1097 December 2025 (REF_DATE 2025-07)"
P_ISED = "ISED SFGSME 2023 survey (calendar 2023)"
U_LOC = "location (employer statistical location)"
U_RATE = "monthly rate (enterprises per active enterprise at t-1)"
LQ_HIGH, LQ_LOW, LQ_MIN_COUNT, LQ_MIN_Z = 1.5, 1 / 1.5, 10, 3.0
SMALL_COUNT = 10
SIZE_BANDS = ["1-4", "5-19", "20-99", "100-499"]
FORBIDDEN_COLS = re.compile(r"score|rank|weight|priorit", re.I)
FORBIDDEN_VALUES = re.compile(r"\brecommend|\bpriorit", re.I)

gates, READS = {}, []


def gate(name, passed, detail=""):
    gates[name] = bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def banner(t):
    print("\n" + "=" * 90 + "\n" + t + "\n" + "=" * 90)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def logged(path):
    READS.append(Path(path).resolve().relative_to(ROOT.resolve()).as_posix())
    return path


def read_mart(con, name, where=""):
    """Read one dbt mart from the warehouse in primary-key order (the order of the validated baseline views)."""
    READS.append(f"{WAREHOUSE.relative_to(ROOT).as_posix()}::analytics.{name}")
    return con.execute(f"select * from analytics.{name} {where} order by {MART_ORDER[name]}").to_arrow_table().to_pandas()


def snapshot():
    hashed = {}
    for base in HASHED_PROTECTED:
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        hashed.update({p.relative_to(ROOT).as_posix(): sha256_file(p) for p in files})
    statted = {p.relative_to(ROOT).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
               for base in STAT_PROTECTED for p in sorted(base.rglob("*")) if p.is_file()}
    return hashed, statted


def frame_hash(df):
    return hashlib.sha256(pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes()
                          + "|".join(df.columns).encode()).hexdigest()


# ---------------------------------------------------
# 1. Upstream integrity
# ---------------------------------------------------

def upstream():
    banner("1. UPSTREAM INTEGRITY")
    for k, p in SUMMARIES.items():
        s = json.loads(Path(logged(p)).read_text(encoding="utf-8")) if p.exists() else {}
        g = s.get("gates", {})
        gate(f"upstream_phase_{k}_passes", bool(g) and all(g.values()) and s.get("all_gates_pass") is True, f"{sum(g.values())} of {len(g)}")
    man = pd.read_csv(VIEWS / "analytical_views_manifest.csv", dtype=str)     # baseline integrity check only, not an analytical input
    ok = {r.view_name: sha256_file(ROOT / r.output_path) == r.file_sha256 for r in man.itertuples()}
    gate("analytical_view_files_match_manifest_hashes", len(ok) == 11 and all(ok.values()) and (man.validation_status == "passed").all(),
         f"{sum(ok.values())} of {len(ok)} views")
    mig = json.loads(Path(logged(MIGRATION_SUMMARY)).read_text(encoding="utf-8")) if MIGRATION_SUMMARY.exists() else {}
    tests = mig.get("dbt_tests") or {}
    rec_path = MIGRATION_SUMMARY.parent / "model_reconciliation.csv"
    reconciled = dict(pd.read_csv(logged(rec_path), dtype=str)[["model", "dbt_row_checksum"]].values) if rec_path.exists() else {}
    current = {}
    if WAREHOUSE.exists():            # integrity check of the relations about to be read (not an analytical input)
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        current = {m: con.execute(f"select sum(hash(t)::hugeint)::varchar from analytics.{m} t").fetchone()[0] for m in MART_ORDER}
        con.close()
    fresh = bool(current) and all(current[m] == reconciled.get(m) for m in MART_ORDER)
    gate("dbt_marts_tested_and_reconciled_with_baseline", WAREHOUSE.exists() and mig.get("all_checks_pass") is True
         and mig.get("models_reconciled") == 11 == mig.get("models_total") and bool(tests.get("tests")) and tests.get("tests") == tests.get("tests_passed")
         and fresh,
         f"{mig.get('models_reconciled')} of {mig.get('models_total')} marts reconciled; dbt tests {tests.get('tests_passed')} of {tests.get('tests')}; "
         f"{sum(current.get(m) == reconciled.get(m) for m in MART_ORDER)} of {len(MART_ORDER)} marts read match their reconciled row checksums")
    f = json.loads(SUMMARIES["F"].read_text(encoding="utf-8"))["facts"]
    return man, f


# ---------------------------------------------------
# 2. Analysis (pure: views in, tables out)
# ---------------------------------------------------

def load_views():
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    v = {n: read_mart(con, n) for n in ("dim_cma", "dim_industry", "dim_size_band", "fact_cma_market_scale_snapshot",
                                        "fact_cma_sector_composition_snapshot", "fact_cma_size_composition_snapshot",
                                        "fact_ised_financing_context")}
    v["momentum"] = read_mart(con, "fact_cma_momentum_monthly", "where industry_code_0722 = '1' and size_band_key in ('TOTAL', '1-4', '5-19')")
    READS.append(f"{WAREHOUSE.relative_to(ROOT).as_posix()}::analytics.fact_cma_momentum_monthly")
    v["momentum_cma_codes"] = np.array([r[0] for r in con.execute(
        "select distinct cma_code_sgc2021 from analytics.fact_cma_momentum_monthly order by 1").fetchall()], dtype=object)
    con.close()
    return v


def tukey(s):
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    return s[(s < lo) | (s > hi)]


def analyze(v):
    t = {}
    dim = v["dim_cma"].copy()
    dim["tier"] = dim.tier.astype(int)
    base = dim[["cma_code_sgc2021", "cma_label_sgc2021", "tier"]]
    elig = set(dim[dim.momentum_comparison_eligible].cma_code_sgc2021)

    # ---- market scale (1097) ----
    sc = v["fact_cma_market_scale_snapshot"].pivot(index="cma_code_sgc2021", columns="size_scope", values="locations")
    sec = v["fact_cma_sector_composition_snapshot"]
    unc = sec[sec.naics_sector == "UNCLASSIFIED"].set_index("cma_code_sgc2021")
    pa = sec[sec.naics_sector == "91"].set_index("cma_code_sgc2021")
    bs = sec[sec.sector_role == "business_sector"].groupby("cma_code_sgc2021").sme_locations.sum()
    scale = base.set_index("cma_code_sgc2021").join(pd.DataFrame({
        "sme_employer_locations": sc.sme_1_499, "all_employer_locations": sc.all_sizes, "employer_locations_500_plus": sc["500_plus"],
        "classified_business_sector_sme_locations": bs, "unclassified_sme_locations": unc.sme_locations,
        "unclassified_all_size_locations": unc.locations_all_sizes, "public_administration_sme_locations": pa.sme_locations}))
    tot_sme = scale.sme_employer_locations.sum()
    scale["sme_share_of_employer_locations"] = scale.sme_employer_locations / scale.all_employer_locations
    scale["share_500_plus_of_employer_locations"] = scale.employer_locations_500_plus / scale.all_employer_locations
    scale["unclassified_share_of_sme_locations"] = scale.unclassified_sme_locations / scale.sme_employer_locations
    scale["unclassified_share_of_all_size_locations"] = scale.unclassified_all_size_locations / scale.all_employer_locations
    scale["public_administration_share_of_sme_locations"] = scale.public_administration_sme_locations / scale.sme_employer_locations
    scale["share_of_41_cma_sme_locations"] = scale.sme_employer_locations / tot_sme
    scale["ratio_to_41_cma_median_sme_locations"] = scale.sme_employer_locations / scale.sme_employer_locations.median()
    scale = scale.reset_index().assign(source_view="fact_cma_market_scale_snapshot; fact_cma_sector_composition_snapshot",
                                       statistical_unit=U_LOC, period=P_1097, measure_label="SME employer locations (1-499 employees)")
    t["scale"] = scale
    srt = scale.sme_employer_locations.sort_values(ascending=False)
    shares = scale.share_of_41_cma_sme_locations
    gini_x = np.sort(scale.sme_employer_locations.values)
    n = len(gini_x)
    t["scale_facts"] = {
        "sme_total_41": int(tot_sme), "all_total_41": int(scale.all_employer_locations.sum()),
        "sme_median": float(scale.sme_employer_locations.median()), "sme_max": int(srt.iloc[0]), "sme_min": int(srt.iloc[-1]),
        "top1_share": float(srt.iloc[0] / tot_sme), "top3_share": float(srt.iloc[:3].sum() / tot_sme),
        "top5_share": float(srt.iloc[:5].sum() / tot_sme), "hhi_across_cmas": float((shares ** 2).sum()),
        "gini_across_cmas": float((2 * np.arange(1, n + 1) - n - 1).dot(gini_x) / (n * gini_x.sum())),
        "cmas_for_half": int((srt.cumsum() / tot_sme < 0.5).sum() + 1),
        "tier_sum": scale.groupby("tier").sme_employer_locations.sum().astype(int).to_dict(),
        "tier_median": scale.groupby("tier").sme_employer_locations.median().to_dict(),
        "tier_share": (scale.groupby("tier").sme_employer_locations.sum() / tot_sme).to_dict()}

    # ---- momentum (0722, T004, all sizes) ----
    mo = v["momentum"]
    s = mo[(mo.size_band_key == "TOTAL") & mo.measure.isin(CORE_MEASURES) & mo.cma_code_sgc2021.isin(elig)].copy()
    s = s.merge(base, on="cma_code_sgc2021", how="left", validate="many_to_one")
    for w, months in (("in_recent_12m", RECENT), ("in_prior_12m", PRIOR), ("in_trailing_36m", W36)):
        s[w] = s.ref_month.isin(months)
    series = s[["cma_code_sgc2021", "cma_code_sgc2016", "cma_label_sgc2021", "tier", "ref_month", "measure", "value", "value_available",
                "value_state", "caution_flag", "pandemic_window_flag", "in_recent_12m", "in_prior_12m", "in_trailing_36m",
                "statistical_unit", "metric_id"]].assign(
        source_view="fact_cma_momentum_monthly", source_id="StatCan 33-10-0722-01", scope_label=MOM_LABEL,
        period="0722 monthly, seasonally adjusted, 2015-01 to 2026-05").sort_values(["cma_code_sgc2021", "measure", "ref_month"]).reset_index(drop=True)
    t["series"] = series
    wide = series.pivot(index=["cma_code_sgc2021", "ref_month"], columns="measure", values="value")

    def wstats(months, tag):
        sub = series[series.ref_month.isin(months)]
        g = sub.groupby(["cma_code_sgc2021", "measure"])
        a = pd.DataFrame({"usable": g.value.count(), "mean": g.value.mean(), "sd": g.value.std(), "total": g.value.sum(min_count=1),
                          "e": g.caution_flag.sum()}).unstack("measure")
        a.columns = [f"{m}_{k}_{tag}" for k, m in a.columns]
        return a
    r, p, w = wstats(RECENT, "12m"), wstats(PRIOR, "prior12m"), wstats(W36, "36m")
    mom = base[base.cma_code_sgc2021.isin(elig)].set_index("cma_code_sgc2021")
    out = pd.DataFrame(index=mom.index)
    for m in RATES:
        ok_r = r[f"{m}_usable_12m"] >= MIN_USABLE_12
        ok_p = p[f"{m}_usable_prior12m"] >= MIN_USABLE_12
        out[f"mean_{m}_12m"] = r[f"{m}_mean_12m"].where(ok_r)
        out[f"mean_{m}_prior_12m"] = p[f"{m}_mean_prior12m"].where(ok_p)
        out[f"change_{m}_recent_vs_prior"] = out[f"mean_{m}_12m"] - out[f"mean_{m}_prior_12m"]
        out[f"direction_{m}"] = np.select([out[f"change_{m}_recent_vs_prior"] > 0, out[f"change_{m}_recent_vs_prior"] < 0,
                                           out[f"change_{m}_recent_vs_prior"] == 0], ["increase", "decrease", "no change"], default="not eligible")
        se = np.sqrt(r[f"{m}_sd_12m"] ** 2 / r[f"{m}_usable_12m"] + p[f"{m}_sd_prior12m"] ** 2 / p[f"{m}_usable_prior12m"])
        out[f"change_{m}_exceeds_2se_heuristic"] = (out[f"change_{m}_recent_vs_prior"].abs() > 2 * se).where(out[f"change_{m}_recent_vs_prior"].notna())
        out[f"usable_months_{m}_12m"] = r[f"{m}_usable_12m"]
    out["sum_openings_12m"] = r["openings_total_12m"]
    out["sum_closures_12m"] = r["closures_total_12m"]
    out["cumulative_net_formation_12m"] = r["net_formation_total_12m"].where(r["net_formation_usable_12m"] >= MIN_USABLE_12)
    out["mean_monthly_net_formation_12m"] = r["net_formation_mean_12m"].where(r["net_formation_usable_12m"] >= MIN_USABLE_12)
    act = wide["active"].unstack("ref_month")
    out["active_enterprises_2026_05"] = act["2026-05"]
    out["active_enterprises_2025_05"] = act["2025-05"]
    out["active_change_2025_05_to_2026_05"] = out.active_enterprises_2026_05 - out.active_enterprises_2025_05
    out["active_growth_12m"] = out.active_change_2025_05_to_2026_05 / out.active_enterprises_2025_05
    out["net_formation_minus_active_change"] = out.cumulative_net_formation_12m - out.active_change_2025_05_to_2026_05
    idx = {m: i for i, m in enumerate(W36)}
    for m in ("net_formation_rate", "opening_rate"):
        slopes, usable = {}, {}
        for c, g in series[(series.measure == m) & series.in_trailing_36m & series.value.notna()].groupby("cma_code_sgc2021"):
            usable[c] = len(g)
            slopes[c] = np.polyfit(g.ref_month.map(idx).values, g.value.values, 1)[0] * 12 if len(g) >= MIN_USABLE_36 else np.nan
        out[f"trend_{m}_36m_per_year"] = pd.Series(slopes)
        out[f"usable_months_{m}_36m"] = pd.Series(usable)
    nfr = wide["net_formation_rate"].unstack("ref_month")
    out["volatility_net_formation_rate_12m_sd"] = r["net_formation_rate_sd_12m"].where(r["net_formation_rate_usable_12m"] >= MIN_USABLE_12)
    d36 = nfr[W36].diff(axis=1).iloc[:, 1:]
    out["volatility_net_formation_rate_36m_mom_change_sd"] = d36.std(axis=1, ddof=1).where(d36.notna().sum(axis=1) >= MIN_USABLE_36 - 1)
    out["e_flagged_observations_12m"] = sum(r[f"{m}_e_12m"] for m in CORE_MEASURES).astype(int)
    out["unavailable_observations_12m"] = sum(len(RECENT) - r[f"{m}_usable_12m"] for m in CORE_MEASURES).astype(int)
    out["e_flagged_observations_36m"] = sum(w[f"{m}_e_36m"] for m in CORE_MEASURES).astype(int)
    out["unavailable_observations_36m"] = sum(len(W36) - w[f"{m}_usable_36m"] for m in CORE_MEASURES).astype(int)
    out["share_usable_months_12m"] = sum(r[f"{m}_usable_12m"] for m in CORE_MEASURES) / (len(CORE_MEASURES) * len(RECENT))
    out["pandemic_months_in_windows"] = int(series[series.pandemic_window_flag & (series.in_recent_12m | series.in_prior_12m | series.in_trailing_36m)].shape[0])
    out["recent_12m_complete"] = pd.concat([r[f"{m}_usable_12m"] for m in RATES], axis=1).min(axis=1) >= MIN_USABLE_12
    momentum = mom.join(out).reset_index().assign(
        scope_label=MOM_LABEL, source_view="fact_cma_momentum_monthly", source_id="StatCan 33-10-0722-01",
        statistical_unit=f"enterprise (counts); {U_RATE} (rates)", period=f"{P_RECENT}; {P_PRIOR}; {P_W36}",
        industry_scope="Business sector industries [T004]", size_scope="all employment sizes (incl. 500+)",
        net_formation_interpretation="cumulative = sum of 12 monthly SA (openings - closures); differs from the change in SA active enterprises (reopenings, exits and separate seasonal adjustment)")
    t["momentum"] = momentum

    # ---- small-business momentum coverage (bands kept separate) ----
    sb = mo[mo.size_band_key.isin(["1-4", "5-19"]) & mo.cma_code_sgc2021.isin(elig) & mo.ref_month.isin(RECENT)]
    g = sb.groupby(["cma_code_sgc2021", "size_band_key", "measure"])
    u = pd.DataFrame({"usable": g.value.count(), "mean": g.value.mean(), "e": g.caution_flag.sum()}).unstack("measure")
    rows = []
    for (c, b) in sorted({(c, b) for c in elig for b in ("1-4", "5-19")}):
        get = lambda k, m: (u.loc[(c, b), (k, m)] if (c, b) in u.index and (k, m) in u.columns else np.nan)  # noqa: E731
        ua, uo, ucl = get("usable", "active"), get("usable", "opening_rate"), get("usable", "closure_rate")
        ua, uo, ucl = [0 if pd.isna(x) else int(x) for x in (ua, uo, ucl)]
        flows = uo >= MIN_USABLE_12 and ucl >= MIN_USABLE_12
        rows.append({"cma_code_sgc2021": c, "size_band_key": b, "usable_active_months_12m": ua, "usable_opening_rate_months_12m": uo,
                     "usable_closure_rate_months_12m": ucl, "active_complete_12m": ua >= MIN_USABLE_12, "flow_rates_complete_12m": flows,
                     "mean_active_enterprises_12m": get("mean", "active") if ua >= MIN_USABLE_12 else np.nan,
                     "mean_opening_rate_12m": get("mean", "opening_rate") if flows else np.nan,
                     "mean_closure_rate_12m": get("mean", "closure_rate") if flows else np.nan,
                     "mean_net_formation_rate_12m": get("mean", "net_formation_rate") if flows else np.nan,
                     "e_flagged_observations_12m": int(np.nansum([get("e", m) for m in CORE_MEASURES]))})
    small = base.merge(pd.DataFrame(rows), on="cma_code_sgc2021").assign(
        scope_label="small-business measure (band kept separate; not an SME total; says nothing about 20-499)",
        source_view="fact_cma_momentum_monthly", statistical_unit=f"enterprise (active); {U_RATE} (rates)", period=P_RECENT)
    t["small"] = small

    # ---- industry composition (1097, 19 business sectors; 91 and Unclassified separate) ----
    labels = v["dim_industry"][v["dim_industry"].industry_system == "NAICS2022_1097"][["industry_code", "industry_label"]]
    d = sec.merge(labels, left_on="naics_sector", right_on="industry_code", how="left", validate="many_to_one").drop(columns="industry_code")
    b19 = d.sector_role == "business_sector"
    cma_cls = d[b19].groupby("cma_code_sgc2021").sme_locations.transform("sum")
    d.loc[b19, "share_recomputed"] = d.loc[b19, "sme_locations"] / cma_cls
    uni = d[b19].groupby("naics_sector").sme_locations.sum() / d[b19].sme_locations.sum()
    d["universe_share_41_cma"] = d.naics_sector.map(uni).where(b19)
    d["location_quotient_vs_41_cma_universe"] = d.share / d.universe_share_41_cma
    d["expected_locations_at_universe_share"] = d.universe_share_41_cma * d.share_denominator.where(b19)
    d["poisson_z"] = (d.sme_locations - d.expected_locations_at_universe_share) / np.sqrt(d.expected_locations_at_universe_share)
    d["small_count_flag"] = d.sme_locations < SMALL_COUNT
    big = np.maximum(d.sme_locations, d.expected_locations_at_universe_share) >= LQ_MIN_COUNT
    d["specialization_flag"] = np.select(
        [b19 & big & (d.location_quotient_vs_41_cma_universe >= LQ_HIGH) & (d.poisson_z >= LQ_MIN_Z),
         b19 & big & (d.location_quotient_vs_41_cma_universe <= LQ_LOW) & (d.poisson_z <= -LQ_MIN_Z)],
        ["over-represented vs 41-CMA universe", "under-represented vs 41-CMA universe"], default="")
    d.loc[~b19, "specialization_flag"] = "not assessed (shown separately)"
    detail = d.merge(base, on="cma_code_sgc2021").assign(source_view="fact_cma_sector_composition_snapshot", statistical_unit=U_LOC,
                                                          period=P_1097, benchmark="41-CMA table-1097 universe (classified 19-sector SME locations)")
    t["sector_detail"] = detail[["cma_code_sgc2021", "cma_label_sgc2021", "tier", "naics_sector", "industry_label", "sector_role", "sme_locations",
                                 "share", "share_recomputed", "share_denominator", "universe_share_41_cma", "location_quotient_vs_41_cma_universe",
                                 "expected_locations_at_universe_share", "poisson_z", "small_count_flag", "specialization_flag",
                                 "omitted_zero_flag", "n_zero_by_reconciliation_cells", "benchmark", "source_view", "statistical_unit", "period"]]
    bd = detail[detail.sector_role == "business_sector"]
    hhi = bd.groupby("cma_code_sgc2021").share.apply(lambda x: float((x ** 2).sum()))
    top = bd.sort_values(["cma_code_sgc2021", "share", "naics_sector"], ascending=[True, False, True]).groupby("cma_code_sgc2021").head(1).set_index("cma_code_sgc2021")
    q1, q3 = hhi.quantile(0.25), hhi.quantile(0.75)
    ind = base.set_index("cma_code_sgc2021").assign(
        sector_hhi=hhi, effective_number_of_sectors=1 / hhi, largest_sector_code=top.naics_sector, largest_sector_label=top.industry_label,
        largest_sector_share=top.share, max_location_quotient=bd.groupby("cma_code_sgc2021").location_quotient_vs_41_cma_universe.max(),
        n_sectors_over_represented=bd.groupby("cma_code_sgc2021").specialization_flag.apply(lambda x: int((x == "over-represented vs 41-CMA universe").sum())),
        n_sectors_under_represented=bd.groupby("cma_code_sgc2021").specialization_flag.apply(lambda x: int((x == "under-represented vs 41-CMA universe").sum())),
        over_represented_sectors=bd[bd.specialization_flag.str.startswith("over")].groupby("cma_code_sgc2021").naics_sector.apply(";".join),
        n_small_count_sectors=bd.groupby("cma_code_sgc2021").small_count_flag.sum().astype(int),
        classified_business_sector_sme_locations=bs)
    ind["concentration_group"] = np.select([ind.sector_hhi >= q3, ind.sector_hhi <= q1], ["more concentrated (HHI >= 41-CMA upper quartile)",
                                            "more diversified (HHI <= 41-CMA lower quartile)"], default="middle half")
    ind["over_represented_sectors"] = ind.over_represented_sectors.fillna("")
    t["industry"] = ind.reset_index().assign(source_view="fact_cma_sector_composition_snapshot", statistical_unit=U_LOC, period=P_1097,
                                            share_basis="classified 1-499 locations in the 19 business sectors (91 and Unclassified excluded)")
    t["industry_facts"] = {"universe_share": uni.to_dict(), "hhi_q1": float(q1), "hhi_q3": float(q3)}

    # ---- size composition (1097) ----
    sz = v["fact_cma_size_composition_snapshot"]
    loc = sz.pivot(index="cma_code_sgc2021", columns="size_band_key", values="locations")
    sh = sz.pivot(index="cma_code_sgc2021", columns="size_band_key", values="share_of_sme_locations")
    size = base.set_index("cma_code_sgc2021")
    for bnd in SIZE_BANDS + ["500-plus"]:
        size[f"locations_{bnd}"] = loc[bnd]
    smet = loc[SIZE_BANDS].sum(axis=1)
    for bnd in SIZE_BANDS:
        size[f"share_{bnd}"] = sh[bnd]
        size[f"share_{bnd}_recomputed"] = loc[bnd] / smet
    size["share_1-19_combined"] = size["share_1-4"] + size["share_5-19"]
    size["share_500-plus_of_all_employer_locations"] = loc["500-plus"] / (smet + loc["500-plus"])
    zc = []
    for bnd in SIZE_BANDS:
        col = size[f"share_{bnd}"]
        size[f"z_share_{bnd}"] = (col - col.mean()) / col.std(ddof=1)
    zcols = [f"z_share_{b}" for b in SIZE_BANDS]
    size["distinct_size_profile_bands"] = size[zcols].apply(lambda r: ";".join(b for b, z in zip(SIZE_BANDS, r) if abs(z) >= 2), axis=1)
    size["distinct_size_profile_flag"] = size.distinct_size_profile_bands != ""
    size["small_count_100_499_lt10"] = size["locations_100-499"] < 10
    size["small_count_100_499_lt30"] = size["locations_100-499"] < 30
    t["size"] = size.reset_index().assign(source_view="fact_cma_size_composition_snapshot", statistical_unit=U_LOC, period=P_1097,
                                         unit_note="location-based employment-size composition; not enterprise size")

    # ---- ISED financing context ----
    iv = v["fact_ised_financing_context"]
    keep = iv.metric_id.notna() | (iv.measure_id == "ISED_T49_M001")
    isd = iv[keep].copy()
    isd["se_value"] = np.where(isd.se_or_cv == "CV", isd.error_value.astype(float) / 100 * isd.value_published.astype(float), isd.error_value.astype(float))
    isd["usable_in_comparison"] = isd.mvp_use_status.isin(["usable_A_to_D", "usable_with_caution_E"])
    nat = isd[isd.segment_dimension == "national"].set_index("measure_id")
    isd["national_value"] = isd.measure_id.map(nat.value_published).astype(float)
    isd["national_se"] = isd.measure_id.map(nat.se_value)
    isd["national_usable"] = isd.measure_id.map(nat.usable_in_comparison)
    comp = isd.usable_in_comparison & isd.national_usable.astype(bool) & (isd.segment_dimension != "national") & isd.se_value.notna()
    isd["difference_vs_national"] = (isd.value_published.astype(float) - isd.national_value).where(comp)
    isd["combined_se_vs_national"] = np.sqrt(isd.se_value ** 2 + isd.national_se ** 2).where(comp)
    isd["distinguishable_from_national"] = (isd.difference_vs_national.abs() > 2 * isd.combined_se_vs_national).where(comp)
    isd["comparison_rule"] = "|difference| > 2 x sqrt(SE1^2 + SE2^2); CV converted as SE = CV/100 x value"
    isd["comparison_caveat"] = np.where(isd.segment_dimension == "national", "national reference row",
                                        np.where(comp, "segment is part of the national population (not independent): conservative test",
                                                 "not compared: " + isd.mvp_use_status))
    isd["value_for_comparison"] = isd.value_usable.astype(float).where(isd.usable_in_comparison)
    t["ised"] = isd[["ised_table", "measure_id", "metric_id", "measure_label", "estimate_type", "unit", "statistical_unit", "universe_or_denominator",
                     "segment_dimension", "segment_key", "segment_label_published", "nested_region_flag", "geography_caveat", "value_published",
                     "value_for_comparison", "value_state", "quality_code", "se_or_cv", "error_value", "se_value", "caution_flag", "excluded_quality_f",
                     "dotdot_flag", "mvp_use_status", "usable_in_comparison", "national_value", "national_se", "difference_vs_national",
                     "combined_se_vs_national", "distinguishable_from_national", "comparison_rule", "comparison_caveat"]].assign(
        source_view="fact_ised_financing_context", period=P_ISED, context_role="contextual only; not a market-score input").sort_values(
        ["measure_id", "segment_dimension", "segment_key"]).reset_index(drop=True)
    pw = {}
    for mid in ("ISED_T01_M001", "ISED_T03_M001", "ISED_T03_M004"):
        c = isd[(isd.measure_id == mid) & (isd.segment_dimension == "cma") & isd.usable_in_comparison]
        vals, ses = c.value_published.astype(float).values, c.se_value.values
        tot = dist = 0
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                tot += 1
                dist += abs(vals[i] - vals[j]) > 2 * np.sqrt(ses[i] ** 2 + ses[j] ** 2)
        pw[mid] = {"pairs": tot, "distinguishable": int(dist), "cmas": len(vals)}
    t["ised_facts"] = {"pairwise": pw}

    # ---- evidence profile (41) ----
    full = mo[(mo.size_band_key == "TOTAL") & mo.measure.isin(["active", "openings", "closures"])]
    fe = full.groupby("cma_code_sgc2021").agg(e_full=("caution_flag", "sum"), unavail_full=("value_available", lambda x: int((~x).sum())))
    ic = isd[isd.segment_dimension == "cma"].groupby("segment_key").agg(
        ised_cma_estimates=("measure_id", "size"), ised_cma_usable=("usable_in_comparison", "sum"),
        ised_cma_E=("caution_flag", "sum"), ised_cma_F=("excluded_quality_f", "sum"))
    zr = detail.groupby("cma_code_sgc2021").omitted_zero_flag.sum()
    ev = dim.set_index("cma_code_sgc2021")[["cma_label_sgc2021", "tier", "evidence_label", "vintage_status", "boundary_change_flag",
                                            "has_momentum_0722", "has_ised_cma_context", "ised_region_key"]]
    mom_i = momentum.set_index("cma_code_sgc2021")
    ev = ev.assign(
        momentum_available=ev.index.isin(mom_i.index), momentum_recent_12m_complete=mom_i.recent_12m_complete.reindex(ev.index),
        momentum_e_flagged_12m=mom_i.e_flagged_observations_12m.reindex(ev.index), momentum_unavailable_12m=mom_i.unavailable_observations_12m.reindex(ev.index),
        momentum_e_flagged_full_history=fe.e_full.reindex(ev.index), momentum_unavailable_full_history=fe.unavail_full.reindex(ev.index),
        market_scale_available=scale.set_index("cma_code_sgc2021").sme_employer_locations.reindex(ev.index).notna(),
        industry_available=detail.groupby("cma_code_sgc2021").size().reindex(ev.index).eq(21),
        size_available=sz.groupby("cma_code_sgc2021").size().reindex(ev.index).eq(5),
        sector_rows_with_zero_by_reconciliation=zr.reindex(ev.index).astype(int),
        ised_cma_context_available=ev.index.isin(ic.index), ised_cma_estimates=ic.ised_cma_estimates.reindex(ev.index),
        ised_cma_usable_estimates=ic.ised_cma_usable.reindex(ev.index), ised_cma_E_estimates=ic.ised_cma_E.reindex(ev.index),
        ised_cma_F_estimates=ic.ised_cma_F.reindex(ev.index),
        source_periods=np.where(ev.index.isin(elig), f"0722 2015-01 to 2026-05 (analysis windows to 2026-05); {P_1097}", P_1097))
    ev["source_periods"] = ev.source_periods + np.where(ev.index.isin(ic.index), "; ISED 2023 (CMA estimates)", "; ISED 2023 at region/national level only (not attached to this CMA)")
    ev["primary_opportunity_analysis_eligibility"] = np.select(
        [ev.tier == 1, ev.tier == 2], ["eligible (Tier 1): momentum + scale + ISED CMA context",
                                       "eligible (Tier 2): momentum + scale; ISED CMA context not available"], default=TIER3_LABEL)
    t["evidence"] = ev.reset_index().assign(source_view="dim_cma; fact_cma_momentum_monthly; fact_cma_market_scale_snapshot; "
                                            "fact_cma_sector_composition_snapshot; fact_cma_size_composition_snapshot; fact_ised_financing_context",
                                            statistical_unit="CMA (evidence attributes)", period="see source_periods")
    return t


# ---------------------------------------------------
# 3. Candidate indicators, distributions, correlations, findings
# ---------------------------------------------------

CANDIDATES = [
    # id, name, role, view, unit, formula, grain, period, tiers, direction, missing, quality, outlier, transform, interp_risk, status, group, column
    ("C01", "SME employer locations", "market scale", "fact_cma_market_scale_snapshot", U_LOC, "sum of 1097 locations in bands 1-4 ... 200-499 (S02)",
     "CMA", P_1097, "1, 2, 3", "hypothesis: larger base = larger addressable location base (to test in Phase I)",
     "none missing (administrative count; omitted cells are proven zeros)", "no sampling error; locations, not businesses",
     "strongly right-skewed (Toronto, Montreal, Vancouver)", "log10", "locations are not firms or clients; multi-location firms counted per location",
     "retain for Phase I testing", "1097", "sme_employer_locations"),
    ("C02", "SME share of employer locations", "market scale", "fact_cma_market_scale_snapshot", "share of locations", "SME locations / all employer locations",
     "CMA", P_1097, "1, 2, 3", "none meaningful (near-constant)", "none missing", "no sampling error", "tiny range", "none",
     "near 1 everywhere; does not discriminate between markets", "reject", "1097", "sme_share_of_employer_locations"),
    ("C03", "Mean monthly opening rate, trailing 12 months", "recent momentum", "fact_cma_momentum_monthly", U_RATE,
     "mean over 2025-06..2026-05 of openings(t) / active(t-1)", "CMA", P_RECENT, "1, 2", "hypothesis: higher = more business formation activity",
     f"requires {MIN_USABLE_12} of 12 usable months, else not eligible", "E-flagged months kept with caution count; x/.. never zero",
     "moderate", "none", "all-employer context, not SME-specific; moves almost one-for-one with the closure rate (churn intensity) and is nearly unrelated to net formation",
     "retain for Phase I testing", "momentum", "mean_opening_rate_12m"),
    ("C04", "Mean monthly closure rate, trailing 12 months", "recent momentum", "fact_cma_momentum_monthly", U_RATE,
     "mean over 2025-06..2026-05 of closures(t) / active(t-1)", "CMA", P_RECENT, "1, 2", "ambiguous: higher = more churn (risk or turnover)",
     f"requires {MIN_USABLE_12} of 12 usable months", "as C03", "moderate", "none", "direction ambiguous; closely tracks opening rate (churn)",
     "supporting context", "momentum", "mean_closure_rate_12m"),
    ("C05", "Mean monthly net-formation rate, trailing 12 months", "recent momentum", "fact_cma_momentum_monthly", U_RATE,
     "mean over 2025-06..2026-05 of (openings(t) - closures(t)) / active(t-1)", "CMA", P_RECENT, "1, 2",
     "hypothesis: higher = expanding employer-business population", f"requires {MIN_USABLE_12} of 12 usable months", "as C03",
     "check extremes", "none", "arithmetic identity with C03 - C04; all-employer context", "retain for Phase I testing", "momentum", "mean_net_formation_rate_12m"),
    ("C06", "Cumulative net formation, trailing 12 months", "recent momentum", "fact_cma_momentum_monthly", "enterprise (net count)",
     "sum over 2025-06..2026-05 of openings(t) - closures(t)", "CMA", P_RECENT, "1, 2", "scale-confounded count", f"requires {MIN_USABLE_12} of 12",
     "as C03", "dominated by large CMAs", "signed log if used", "measures size as much as momentum; not equal to change in active enterprises",
     "supporting context", "momentum", "cumulative_net_formation_12m"),
    ("C07", "Active-enterprise growth, 2025-05 to 2026-05", "recent momentum", "fact_cma_momentum_monthly", "share (change in active enterprises)",
     "active(2026-05) / active(2025-05) - 1", "CMA", "0722 monthly SA; 2025-05 and 2026-05", "1, 2", "hypothesis: higher = growing stock",
     "requires both endpoints published", "endpoint E flags counted", "check extremes", "none",
     "stock change includes reopenings, exits and SA revisions; near-duplicate of C05 across CMAs (use as a cross-check)", "supporting context", "momentum", "active_growth_12m"),
    ("C08", "Change in mean net-formation rate, recent vs prior 12 months", "momentum acceleration", "fact_cma_momentum_monthly", "rate difference (monthly rate units)",
     "mean(2025-06..2026-05) - mean(2024-06..2025-05) of net-formation rate", "CMA", f"{P_RECENT} vs {P_PRIOR}", "1, 2",
     "hypothesis: positive = improving momentum", "requires both windows complete", "2-SE heuristic flag (autocorrelation not modelled)",
     "sensitive to single months", "none", "regression to the mean against recent level; no CMA change exceeds the 2-SE noise heuristic", "unresolved", "momentum",
     "change_net_formation_rate_recent_vs_prior"),
    ("C09", "36-month linear trend in net-formation rate", "momentum acceleration", "fact_cma_momentum_monthly", "rate change per year",
     "OLS slope of monthly net-formation rate on month index over 2023-06..2026-05, x12", "CMA", P_W36, "1, 2",
     "hypothesis: positive = strengthening trend", f"requires {MIN_USABLE_36} of 36 usable months", "as C03", "sensitive to endpoints",
     "none", "linear trend on SA series; not a forecast", "retain for Phase I testing", "momentum", "trend_net_formation_rate_36m_per_year"),
    ("C10", "Volatility of monthly net-formation rate (12-month SD)", "stability", "fact_cma_momentum_monthly", "rate SD (monthly rate units)",
     "sample SD of monthly net-formation rate over 2025-06..2026-05", "CMA", P_RECENT, "1, 2", "hypothesis: lower = more stable (to test)",
     f"requires {MIN_USABLE_12} of 12", "as C03", "right-skewed; small CMAs noisier", "log if skewed",
     "partly reflects CMA size (smaller denominators are noisier)", "retain for Phase I testing", "momentum", "volatility_net_formation_rate_12m_sd"),
    ("C11", "Sector concentration (HHI of 19 business-sector shares)", "industry diversity", "fact_cma_sector_composition_snapshot", "index (0-1) of location shares",
     "sum over 19 sectors of (sector SME locations / classified 19-sector SME locations)^2", "CMA", P_1097, "1, 2, 3",
     "ambiguous: lower = more diversified base (hypothesis: resilience)", "none missing", "Unclassified and 91 excluded from denominator",
     "moderate", "none", "structural attribute; very narrow range across CMAs (little discrimination); direction must be set in Phase I", "supporting context", "1097", "sector_hhi"),
    ("C12", "Maximum sector location quotient (vs 41-CMA universe)", "sector specialization", "fact_cma_sector_composition_snapshot", "ratio of shares",
     "max over 19 sectors of CMA sector share / 41-CMA universe sector share", "CMA", P_1097, "1, 2, 3", "non-directional (describes specialization)",
     "none missing", "small-count sectors can inflate LQ; see specialization flags", "sensitive to small sectors", "log",
     "specialization is sector-specific; better used for sector targeting than market comparison", "supporting context", "1097", "max_location_quotient"),
    ("C13", "Micro-location share (1-4 employees) of SME locations", "size composition", "fact_cma_size_composition_snapshot", "share of locations",
     "1-4 locations / SME locations", "CMA", P_1097, "1, 2, 3", "unresolved: depends on the product focus chosen in Phase I", "none missing",
     "location-based, not enterprise size", "low", "none", "location share, not firm size; direction depends on strategy", "unresolved", "1097", "share_1-4"),
    ("C14", "Small-location share (1-19 employees) of SME locations", "size composition", "fact_cma_size_composition_snapshot", "share of locations",
     "(1-4 + 5-19 locations) / SME locations", "CMA", P_1097, "1, 2, 3", "as C13", "none missing", "as C13", "low", "none",
     "near-duplicate of C13 and mirror of C15 + C16", "reject", "1097", "share_1-19_combined"),
    ("C15", "20-99 employee share of SME locations", "size composition", "fact_cma_size_composition_snapshot", "share of locations",
     "20-99 locations / SME locations", "CMA", P_1097, "1, 2, 3", "hypothesis: higher = more established-SME locations", "none missing",
     "as C13", "low", "none", "location-based", "retain for Phase I testing", "1097", "share_20-99"),
    ("C16", "100-499 employee share of SME locations", "size composition", "fact_cma_size_composition_snapshot", "share of locations",
     "100-499 locations / SME locations", "CMA", P_1097, "1, 2, 3", "hypothesis: higher = more mid-market locations", "none missing",
     "smallest counts of any SME band", "check small CMAs", "none", "sparse at CMA x sector (Phase F); location-based", "supporting context", "1097", "share_100-499"),
    ("C17", "Unclassified share of SME locations", "evidence-quality guardrail", "fact_cma_sector_composition_snapshot", "share of locations",
     "Unclassified 1-499 locations / SME locations (S05)", "CMA", P_1097, "1, 2, 3", "higher = weaker industry evidence (guardrail, not attractiveness)",
     "none missing", "never allocated to sectors", "check extremes", "none", "quality attribute, not a market attribute", "supporting context", "1097",
     "unclassified_share_of_sme_locations"),
    ("C18", "Public-administration share of SME locations", "evidence-quality guardrail", "fact_cma_sector_composition_snapshot", "share of locations",
     "NAICS 91 1-499 locations / SME locations", "CMA", P_1097, "1, 2, 3", "context only (outside SME banking scope)", "none missing",
     "excluded from sector-share denominator", "capital-region outliers", "none", "reflects government presence, not SME market", "supporting context",
     "1097", "public_administration_share_of_sme_locations"),
    ("C19", "Momentum E-flag and unavailable exposure, trailing 12 months", "evidence-quality guardrail", "fact_cma_momentum_monthly",
     "count of observations", "E-flagged + unavailable observations across the 7 core series, 2025-06..2026-05", "CMA", P_RECENT, "1, 2",
     "higher = weaker momentum evidence (guardrail)", "not applicable", "counts statuses; no imputation", "mostly zero", "none",
     "quality guardrail, not attractiveness", "supporting context", "momentum", "quality_exposure_12m"),
    ("C20", "Small-business opening rate, 1-4 employees, trailing 12 months", "recent momentum", "fact_cma_momentum_monthly", U_RATE,
     "mean over 2025-06..2026-05 of 1-4 openings(t) / 1-4 active(t-1)", "CMA x band", P_RECENT, "1, 2",
     "hypothesis: higher = more micro-enterprise formation", f"requires {MIN_USABLE_12} of 12 usable months", "as C03", "moderate", "none",
     "band-specific; not an SME total; near-duplicate of the all-size opening rate C03", "supporting context", "momentum", "sb_opening_rate_1-4"),
    ("C21", "Small-business opening rate, 5-19 employees, trailing 12 months", "recent momentum", "fact_cma_momentum_monthly", U_RATE,
     "mean over 2025-06..2026-05 of 5-19 openings(t) / 5-19 active(t-1)", "CMA x band", P_RECENT, "1, 2 (22 CMAs publish flows)",
     "hypothesis: higher = more small-enterprise formation", "not published for 13 CMAs: unavailable, never zero", "as C03", "moderate", "none",
     "partial coverage would bias a 35-CMA comparison", "unresolved", "momentum", "sb_opening_rate_5-19"),
    ("C22", "ISED external-financing request rate (CMA)", "financing context", "fact_ised_financing_context", "survey estimate (% of SMEs)",
     "published Table 1 estimate", "CMA (Tier 1 only)", P_ISED, "1", "context: share of SMEs seeking external financing",
     "Tier 2/3: not available at CMA level (never inferred)", "A-D usable; E caution; F excluded; '..' unavailable", "sampling error dominates",
     "none", "survey of SMEs with >= $30k revenue; different unit and year; few CMA differences distinguishable", "supporting context", "ised",
     "ised_I01_request_rate"),
    ("C23", "ISED debt request rate (CMA)", "financing context", "fact_ised_financing_context", "survey estimate (% of SMEs)",
     "published Table 3 estimate", "CMA (Tier 1 only)", P_ISED, "1", "context: share of SMEs requesting debt", "as C22", "as C22",
     "sampling error dominates", "none", "as C22", "supporting context", "ised", "ised_I02_debt_request_rate"),
    ("C24", "ISED debt approval rate (CMA)", "financing context", "fact_ised_financing_context", "survey estimate (% of requesters)",
     "published Table 3 overall approval rate", "CMA (Tier 1 only)", P_ISED, "1", "context: approval among requesters", "as C22", "as C22",
     "small requester samples", "none", "universe is requesters, not all SMEs; not comparable with C22/C23 as the same quantity", "supporting context",
     "ised", "ised_I03_debt_approval_rate"),
    ("C25", "ISED product-level approval rates (CMA)", "financing context", "fact_ised_financing_context", "survey estimate (% of requesters)",
     "published Table 4 approval rates by product", "CMA (Tier 1 only)", P_ISED, "1", "context", "as C22", "F-rated cells excluded",
     "not assessed", "none", "F and E estimates concentrate here at CMA level", "reject", "ised_other", None),
    ("C26", "ISED requested and authorized amounts (national and size band)", "financing context", "fact_ised_financing_context",
     "survey estimate (dollars)", "published Table 5 estimates", "national; size band", P_ISED, "none at CMA level", "context: deal size by band",
     "not available by CMA (54.5% F at CMA level)", "F excluded; E caution", "not assessed", "none", "cannot be attached to CMAs",
     "supporting context", "ised_other", None),
    ("C27", "Sector-level momentum (0722 sector members)", "recent momentum", "fact_cma_momentum_monthly", U_RATE, "M01-M07 by 0722 sector member",
     "CMA x sector", "0722 monthly from 2018-01", "8 CMAs only", "not assessed", "not published for 27 of 35 CMAs", "as C03", "not assessed", "none",
     "coverage too narrow for a market comparison", "reject", "other", None),
    ("C28", "2016-2021 CMA boundary caveat", "evidence-quality guardrail", "dim_cma", "flag", "E1 boundary_change_flag", "CMA",
     "SGC 2016 vs 2021", "1, 2, 3", "context (applies to every cross-source CMA view)", "not applicable", "never treated as exact",
     "not applicable", "none", "all 35 shared CMAs carry it; not discriminating", "supporting context", "other", None),
]
CAND_COLS = ["indicator_id", "indicator_name", "analytical_role", "authoritative_source_view", "statistical_unit", "formula", "grain", "period",
             "eligible_tiers", "directionality", "missing_value_treatment", "quality_treatment", "outlier_sensitivity", "possible_transformation",
             "interpretation_risk", "recommended_status", "row_basis", "profile_column"]
RATIONALE = {
    "market scale": "Size of the addressable SME employer-location base in a market",
    "recent momentum": "Whether the employer-business population is currently forming or shrinking",
    "momentum acceleration": "Whether recent momentum is improving or weakening",
    "stability": "How predictable month-to-month business formation is",
    "industry diversity": "How spread the SME location base is across sectors",
    "sector specialization": "Which sectors a market over-represents relative to the 41-CMA universe",
    "size composition": "Mix of micro, small and mid-sized SME locations",
    "evidence-quality guardrail": "How much the evidence for a market can be trusted",
    "financing context": "Survey context on SME financing demand and outcomes",
}


def indicator_frame(t):
    f = t["scale"].set_index("cma_code_sgc2021")[["cma_label_sgc2021", "tier", "sme_employer_locations", "sme_share_of_employer_locations",
                                                  "unclassified_share_of_sme_locations", "public_administration_share_of_sme_locations"]]
    m = t["momentum"].set_index("cma_code_sgc2021")
    for c in ("mean_opening_rate_12m", "mean_closure_rate_12m", "mean_net_formation_rate_12m", "cumulative_net_formation_12m", "active_growth_12m",
              "change_net_formation_rate_recent_vs_prior", "trend_net_formation_rate_36m_per_year", "volatility_net_formation_rate_12m_sd"):
        f[c] = m[c]
    f["quality_exposure_12m"] = m.e_flagged_observations_12m + m.unavailable_observations_12m
    f["e_flagged_12m"] = m.e_flagged_observations_12m
    i = t["industry"].set_index("cma_code_sgc2021")
    f["sector_hhi"], f["max_location_quotient"] = i.sector_hhi, i.max_location_quotient
    s = t["size"].set_index("cma_code_sgc2021")
    for c in ("share_1-4", "share_1-19_combined", "share_20-99", "share_100-499"):
        f[c] = s[c]
    sb = t["small"]
    for b in ("1-4", "5-19"):
        f[f"sb_opening_rate_{b}"] = sb[sb.size_band_key == b].set_index("cma_code_sgc2021").mean_opening_rate_12m
    isd = t["ised"]
    for mid, col in (("ISED_T01_M001", "ised_I01_request_rate"), ("ISED_T03_M001", "ised_I02_debt_request_rate"),
                     ("ISED_T03_M004", "ised_I03_debt_approval_rate")):
        c = isd[(isd.measure_id == mid) & (isd.segment_dimension == "cma")].set_index("segment_key")
        f[col] = c.value_for_comparison
        f[col + "__E"] = c.caution_flag
    return f


def distributions(t, f):
    rows = []
    for cid, name, role, view, unit, *_rest in CANDIDATES:
        col = dict(zip(CAND_COLS, (cid, name, role, view, unit, *_rest)))["profile_column"]
        if col is None:
            continue
        period = dict(zip(CAND_COLS, (cid, name, role, view, unit, *_rest)))["period"]
        basis = dict(zip(CAND_COLS, (cid, name, role, view, unit, *_rest)))["row_basis"]
        eligible = f.index[f.tier <= (1 if basis == "ised" else 2 if basis == "momentum" else 3)]
        x = f.loc[eligible, col].astype(float)
        xv = x.dropna()
        ext = tukey(xv) if len(xv) >= 4 else xv.iloc[0:0]
        e_count = int(f.loc[eligible, "e_flagged_12m"].fillna(0).sum()) if basis == "momentum" else (
            int(f.loc[xv.index, col + "__E"].fillna(False).sum()) if basis == "ised" else 0)
        qual = []
        for c in ext.index:
            if basis == "momentum" and (f.loc[c, "e_flagged_12m"] or 0) > 0:
                qual.append(f"{c}:possible quality influence (E-flagged months)")
            elif basis == "ised" and bool(f.loc[c, col + "__E"]):
                qual.append(f"{c}:E-quality estimate")
            else:
                qual.append(f"{c}:no quality flag (treat as genuine market attribute pending review)")
        mean_ex = xv.drop(ext.index).mean() if len(ext) else np.nan
        rows.append({
            "indicator_id": cid, "indicator_name": name, "profile_column": col, "source_view": view, "statistical_unit": unit, "period": period,
            "eligible_cmas": len(eligible), "count": int(xv.size), "coverage": xv.size / len(eligible), "missing_count": int(x.isna().sum()),
            "zero_count": int((xv == 0).sum()), "e_flag_count": e_count, "minimum": xv.min(), "maximum": xv.max(), "mean": xv.mean(),
            "median": xv.median(), "standard_deviation": xv.std(ddof=1), "q1": xv.quantile(0.25), "q3": xv.quantile(0.75),
            "interquartile_range": xv.quantile(0.75) - xv.quantile(0.25), "median_absolute_deviation": stats.median_abs_deviation(xv),
            "skewness": stats.skew(xv, bias=False) if len(xv) > 2 else np.nan,
            "skewness_log10": stats.skew(np.log10(xv), bias=False) if (xv > 0).all() and len(xv) > 2 else np.nan,
            "extreme_observations_tukey": ";".join(f"{c}={ext[c]:.6g}" for c in ext.index),
            "extreme_observation_quality": ";".join(qual), "mean_excluding_extremes": mean_ex,
            "mean_shift_if_extremes_excluded_pct": (mean_ex / xv.mean() - 1) * 100 if len(ext) and xv.mean() != 0 else 0.0,
            "outlier_treatment_recommendation": ("review robust scaling (median/IQR) or log transform in Phase I; original values preserved"
                                                 if len(ext) else "no Tukey extremes; original values preserved")})
    return pd.DataFrame(rows)


PAIR_NOTES = {
    frozenset(["C03", "C05"]): "arithmetic identity: net-formation rate = opening rate - closure rate",
    frozenset(["C04", "C05"]): "arithmetic identity: net-formation rate = opening rate - closure rate",
    frozenset(["C03", "C04"]): "churn: openings and closures move together",
    frozenset(["C05", "C08"]): "recent level vs change: regression-to-the-mean risk",
    frozenset(["C13", "C14"]): "nested definition (1-19 contains 1-4)",
    frozenset(["C01", "C06"]): "cumulative count is scale-confounded",
    frozenset(["C22", "C23"]): "both shares of all SMEs in the segment (same survey universe)",
}


def correlations(f):
    q = [(c[0], c[-1], c[-2], c[4], c[7]) for c in CANDIDATES if c[-1] is not None]
    rows = []
    for i in range(len(q)):
        for j in range(i + 1, len(q)):
            (a, ca, ga, ua, pa), (b, cb, gb, ub, pb) = q[i], q[j]
            if ("ised" in (ga, gb)) and ga != gb:
                continue                                          # survey estimates are not paired with administrative counts
            max_tier = 1 if "ised" in (ga, gb) else 2 if "momentum" in (ga, gb) else 3
            d = f[f.tier <= max_tier][[ca, cb]].astype(float)
            if ca == "sme_employer_locations":
                d[ca] = np.log10(d[ca])
            if cb == "sme_employer_locations":
                d[cb] = np.log10(d[cb])
            d = d.dropna()
            if len(d) < 8:
                continue
            sp = stats.spearmanr(d[ca], d[cb]).statistic
            pe = stats.pearsonr(d[ca], d[cb]).statistic
            note = PAIR_NOTES.get(frozenset([a, b]), "")
            rows.append({"indicator_a": a, "indicator_b": b, "column_a": ca, "column_b": cb, "n": len(d),
                         "row_basis": {1: "24 Tier 1 CMAs (ISED CMA estimates, usable only)", 2: "35 CMAs (Tiers 1-2)", 3: "41 CMAs (table 1097)"}[max_tier],
                         "spearman_rho": sp, "pearson_r": pe, "pearson_basis": "log10 for SME employer locations",
                         "association_type": "same-source relationship" if ga == gb else "association between market attributes (different units)",
                         "statistical_unit_a": ua, "statistical_unit_b": ub, "period_a": pa, "period_b": pb,
                         "redundancy_flag": "high (|rho| >= 0.8): review in Phase I" if abs(sp) >= 0.8 else ("moderate (0.5-0.8)" if abs(sp) >= 0.5 else ""),
                         "structural_note": note, "source_view": "see candidate register",
                         "statistical_unit": f"{ua} | {ub}", "period": f"{pa} | {pb}"})
    return pd.DataFrame(rows)


# ---------------------------------------------------
# 4. Figures
# ---------------------------------------------------

TIER_COLORS = {1: "#1f5f8b", 2: "#e08a1e", 3: "#7f7f7f"}
SRC_NOTE = {"1097": "Source: Statistics Canada table 33-10-1097-01 (via Phase G view). Counts are employer locations, not businesses.",
            "0722": "Source: Statistics Canada table 33-10-0722-01 (via Phase G view). All-employer-business momentum context, not SME-specific.",
            "ised": "Source: ISED Survey on Financing and Growth of SMEs 2023 (via Phase G view). F estimates excluded; E shown with caution."}


def figure_specs(t, f):
    specs = []
    sc = t["scale"]
    mom = t["momentum"]

    def fig1():
        fig, ax = plt.subplots(figsize=(10, 4.8))
        for tier in (1, 2, 3):
            d = sc[sc.tier == tier]
            ax.scatter(d.sme_employer_locations, np.full(len(d), 4 - tier) + np.linspace(-0.15, 0.15, len(d)), color=TIER_COLORS[tier], s=40,
                       label=f"Tier {tier} ({len(d)} CMAs)")
        for _, r in sc.nlargest(5, "sme_employer_locations").iterrows():
            ax.annotate(r.cma_label_sgc2021, (r.sme_employer_locations, 4 - r.tier), textcoords="offset points", xytext=(0, 8), fontsize=7, ha="center")
        ax.set_xscale("log")
        ax.set_yticks([3, 2, 1], ["Tier 1", "Tier 2", "Tier 3"])
        return fig, ax
    specs.append(("fig01_sme_location_scale_distribution", fig1, "SME employer locations (1-499 employees) across 41 CMAs, December 2025",
                  "SME employer locations (count, log scale)", "Evidence tier", "count, log scale", "December 2025", "1097"))

    def fig2():
        fig, ax = plt.subplots(figsize=(8, 6))
        for tier in (1, 2):
            d = mom[mom.tier == tier]
            ax.scatter(d.mean_opening_rate_12m * 100, d.mean_net_formation_rate_12m * 100, color=TIER_COLORS[tier], s=40, label=f"Tier {tier}")
        ax.axhline(0, color="black", lw=0.6)
        return fig, ax
    specs.append(("fig02_opening_vs_net_formation_rate", fig2,
                  "Opening rate vs net-formation rate, 35 CMAs, trailing 12 months 2025-06 to 2026-05",
                  "Mean monthly opening rate (% of active enterprises at t-1)", "Mean monthly net-formation rate (% of active enterprises at t-1)",
                  "% of active enterprises at t-1", "2025-06 to 2026-05", "0722"))

    def fig3():
        fig, (ax, ax3) = plt.subplots(2, 1, figsize=(9, 7), gridspec_kw={"height_ratios": [5, 1]}, sharex=True)
        m = mom.merge(sc[["cma_code_sgc2021", "sme_employer_locations"]], on="cma_code_sgc2021")
        for tier in (1, 2):
            d = m[m.tier == tier]
            ax.scatter(d.sme_employer_locations, d.mean_net_formation_rate_12m * 100, color=TIER_COLORS[tier], s=40, label=f"Tier {tier}")
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xscale("log")
        d3 = sc[sc.tier == 3]
        ax3.scatter(d3.sme_employer_locations, np.zeros(len(d3)), color=TIER_COLORS[3], marker="|", s=200, label="Tier 3")
        ax3.set_yticks([])
        ax3.set_title("Tier 3 (6 CMAs): scale only - no momentum data (not plotted on the rate axis)", fontsize=8)
        ax3.set_xlabel("SME employer locations (count, log scale), December 2025")
        return fig, ax
    specs.append(("fig03_scale_vs_net_formation_rate", fig3,
                  "SME employer-location scale (December 2025) vs net-formation rate (2025-06 to 2026-05)",
                  "SME employer locations (count, log scale), December 2025", "Net-formation rate (% of active enterprises at t-1)",
                  "count, log scale; % of active enterprises at t-1", "December 2025; 2025-06 to 2026-05", "1097+0722"))

    def fig4():
        d = t["sector_detail"]
        d = d[d.sector_role == "business_sector"]
        piv = np.log2(d.pivot(index="cma_code_sgc2021", columns="naics_sector", values="location_quotient_vs_41_cma_universe"))
        small = d.pivot(index="cma_code_sgc2021", columns="naics_sector", values="small_count_flag")
        order = sc.sort_values(["tier", "cma_code_sgc2021"]).cma_code_sgc2021
        piv, small = piv.loc[order], small.loc[order]
        lab = sc.set_index("cma_code_sgc2021").loc[order]
        fig, ax = plt.subplots(figsize=(11, 12))
        im = ax.imshow(piv.values, cmap="RdBu_r", vmin=-2, vmax=2, aspect="auto")
        ys, xs = np.where(small.values)
        ax.scatter(xs, ys, marker="x", color="black", s=12, label=f"fewer than {SMALL_COUNT} locations")
        ax.set_xticks(range(piv.shape[1]), piv.columns, rotation=90, fontsize=7)
        ax.set_yticks(range(piv.shape[0]), [f"T{tr} {l}" for tr, l in zip(lab.tier, lab.cma_label_sgc2021)], fontsize=7)
        fig.colorbar(im, ax=ax, label="log2 location quotient (0 = 41-CMA universe share)")
        return fig, ax
    specs.append(("fig04_sector_specialization_heatmap", fig4,
                  "Sector specialization: location quotient vs 41-CMA universe, SME locations, December 2025",
                  "NAICS 2022 business sector (cell colour: log2 location quotient vs 41-CMA universe)", "CMA (T = evidence tier)", "log2 location quotient", "December 2025", "1097"))

    def fig5():
        s = t["size"].sort_values(["tier", "share_1-4"])
        fig, ax = plt.subplots(figsize=(11, 5.5))
        bottom = np.zeros(len(s))
        for b, col in zip(SIZE_BANDS, ["#c6dbef", "#6baed6", "#2171b5", "#08306b"]):
            ax.bar(range(len(s)), s[f"share_{b}"] * 100, bottom=bottom, color=col, label=f"{b} employees")
            bottom += s[f"share_{b}"].values * 100
        ax.set_xticks(range(len(s)), [f"T{tr} {l}" for tr, l in zip(s.tier, s.cma_label_sgc2021)], rotation=90, fontsize=6)
        ax.set_ylim(0, 100)
        return fig, ax
    specs.append(("fig05_size_composition", fig5, "Employment-size composition of SME locations by CMA, December 2025 (500+ shown separately in tables)",
                  "CMA (T = evidence tier)", "Share of SME employer locations (%)", "Share of SME employer locations (%)", "December 2025", "1097"))

    def fig6():
        e = t["evidence"].sort_values(["tier", "cma_code_sgc2021"])
        cols = [("momentum_available", "Momentum (0722)"), ("market_scale_available", "Scale (1097)"), ("industry_available", "Industry (1097)"),
                ("size_available", "Size (1097)"), ("ised_cma_context_available", "ISED CMA context")]
        mat = np.array([[1.0 if bool(r[c]) else 0.0 for c, _ in cols] + [0.5 if (r.momentum_e_flagged_12m or 0) > 0 else 1.0 if r.momentum_available else 0.0]
                        for _, r in e.iterrows()])
        fig, ax = plt.subplots(figsize=(8, 12))
        ax.imshow(mat, cmap="Greens", vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(cols) + 1), [n for _, n in cols] + ["Momentum 12m free of E flags"], rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(e)), [f"T{tr} {l}" for tr, l in zip(e.tier, e.cma_label_sgc2021)], fontsize=7)
        return fig, ax
    specs.append(("fig06_evidence_coverage_matrix", fig6, "Evidence coverage by CMA and source (0722 to 2026-05; 1097 December 2025; ISED 2023)",
                  "Evidence element (dark = available, light = partial/caution, white = not available)", "CMA (T = evidence tier)",
                  "availability (categorical)", "0722 to 2026-05; 1097 December 2025; ISED 2023", "all"))

    def fig7():
        i = t["ised"]
        fig, axes = plt.subplots(1, 2, figsize=(12, 7), sharey=False)
        for ax, mid, title in zip(axes, ("ISED_T01_M001", "ISED_T03_M001"), ("External-financing request rate", "Debt request rate")):
            d = i[(i.measure_id == mid) & i.segment_dimension.isin(["cma", "national"])].copy()
            d = d.merge(sc[["cma_code_sgc2021", "cma_label_sgc2021"]], left_on="segment_key", right_on="cma_code_sgc2021", how="left")
            d["name"] = np.where(d.segment_dimension == "national", "Canada (all SMEs)", d.cma_label_sgc2021)
            d = d[d.usable_in_comparison].sort_values("segment_key")
            y = np.arange(len(d))
            colors = ["black" if n == "Canada (all SMEs)" else ("#d62728" if c else TIER_COLORS[1]) for n, c in zip(d.name, d.caution_flag)]
            ax.errorbar(d.value_published.astype(float), y, xerr=2 * d.se_value, fmt="none", ecolor="grey", lw=0.8)
            ax.scatter(d.value_published.astype(float), y, c=colors, s=25)
            ax.set_yticks(y, [n + (" (E)" if c else "") for n, c in zip(d.name, d.caution_flag)], fontsize=7)
            ax.set_title(title + " - % of SMEs, 2023 (bars = +/- 2 SE)", fontsize=9)
            ax.set_xlabel("Survey estimate (% of SMEs), 2023")
        return fig, axes[0]
    specs.append(("fig07_ised_financing_context", fig7, "ISED financing-demand context by Tier 1 CMA, 2023 survey (F excluded; E flagged)",
                  "Survey estimate (% of SMEs), 2023", "Tier 1 CMA or national estimate", "% of SMEs", "2023", "ised"))
    return specs


def render(spec):
    fid, fn, title, xl, yl, unit, period, src = spec
    fig, ax = fn()
    fig.suptitle(title, fontsize=11)
    if fid not in ("fig03_scale_vs_net_formation_rate",):
        ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    if fid in ("fig01_sme_location_scale_distribution", "fig02_opening_vs_net_formation_rate", "fig03_scale_vs_net_formation_rate",
               "fig04_sector_specialization_heatmap", "fig05_size_composition"):
        ax.legend(fontsize=7, loc="best")
    note = " ".join(SRC_NOTE[k] for k in ("1097", "0722", "ised") if k in src or src == "all") + " Descriptive diagnostic; not a ranking."
    fig.text(0.01, 0.005, note, fontsize=6.5, wrap=True, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, metadata={"Software": None})
    plt.close(fig)
    return buf.getvalue(), {"figure_id": fid, "file": f"outputs/phase_h_market_exploration/{fid}.png", "title": title, "x_label": xl,
                            "y_label": yl, "unit": unit, "period": period, "source_note": note}


# ---------------------------------------------------
# 5. Findings
# ---------------------------------------------------

def findings(t, f, dist, corr, facts):
    sc, mo, ind, size, isd = t["scale"], t["momentum"], t["industry"], t["size"], t["ised"]
    lab = sc.set_index("cma_code_sgc2021").cma_label_sgc2021
    F = []

    def add(q, stmt, measure, view, unit, period, market, sv, cv, quality, limit, label, impl):
        F.append({"finding_id": f"H{len(F) + 1:02d}", "business_question": q, "finding_statement": stmt, "evidence_measure": measure, "source_view": view,
                  "statistical_unit": unit, "period": period, "affected_market_or_segment": market, "supporting_value": sv, "comparison_value": cv,
                  "quality_status": quality, "limitation": limit, "evidence_label": label, "implication_for_phase_i": impl})
    sf = t["scale_facts"]
    big = sc.loc[sc.sme_employer_locations.idxmax()]
    add("Market scale", f"The 41 CMAs contain {sf['sme_total_41']:,} SME employer locations; the median CMA has {sf['sme_median']:,.0f} and the largest ({big.cma_label_sgc2021}) has {sf['sme_max']:,}.",
        "C01 SME employer locations", "fact_cma_market_scale_snapshot", U_LOC, P_1097, "41 CMAs", sf["sme_max"], sf["sme_median"],
        "administrative count; no suppression", "locations, not businesses or clients", "FACT: DATA", "scale needs a log or rank-free robust transform before any comparison")
    add("Market scale", f"SME locations are concentrated: the 3 largest CMAs hold {sf['top3_share']:.1%} and the 5 largest {sf['top5_share']:.1%} of the 41-CMA total; {sf['cmas_for_half']} CMAs account for half.",
        "share of 41-CMA SME locations", "fact_cma_market_scale_snapshot", U_LOC, P_1097, "41 CMAs", round(sf["top5_share"], 4), round(sf["top3_share"], 4),
        "administrative count", "CMA boundaries differ in extent; 41 CMAs are not all of Canada", "FACT: DATA",
        "a raw-count scale indicator would be dominated by 3 markets")
    add("Market scale", f"The 1-499 band is {sc.sme_share_of_employer_locations.min():.2%} to {sc.sme_share_of_employer_locations.max():.2%} of employer locations in every CMA.",
        "C02 SME share of employer locations", "fact_cma_market_scale_snapshot", "share of locations", P_1097, "41 CMAs",
        round(sc.sme_share_of_employer_locations.min(), 4), round(sc.sme_share_of_employer_locations.max(), 4), "administrative count",
        "500+ locations are few; share is near-constant", "FACT: DATA", "C02 does not discriminate between markets (rejected)")
    u = sc.set_index("cma_code_sgc2021").unclassified_share_of_sme_locations
    add("Market scale", f"Unclassified locations are {u.median():.1%} of SME locations at the median CMA (range {u.min():.1%} to {u.max():.1%}; highest: {lab[u.idxmax()]}).",
        "C17 Unclassified share", "fact_cma_sector_composition_snapshot", "share of locations", P_1097, lab[u.idxmax()], round(u.max(), 4), round(u.median(), 4),
        "administrative count", "Unclassified is never allocated to sectors", "FACT: DATA", "use as an industry-evidence guardrail")
    pa = sc.set_index("cma_code_sgc2021").public_administration_share_of_sme_locations
    add("Market scale", f"Public administration (NAICS 91) is {pa.median():.2%} of SME locations at the median CMA, up to {pa.max():.2%} ({lab[pa.idxmax()]}); it is excluded from the 19-sector denominator.",
        "C18 public-administration share", "fact_cma_sector_composition_snapshot", "share of locations", P_1097, lab[pa.idxmax()], round(pa.max(), 4),
        round(pa.median(), 4), "administrative count", "outside SME banking scope", "FACT: DATA", "context only")
    tm = sf["tier_median"]
    add("Market scale", f"Median SME locations: Tier 1 {tm[1]:,.0f}, Tier 2 {tm[2]:,.0f}, Tier 3 {tm[3]:,.0f}; Tier 1 holds {sf['tier_share'][1]:.1%} of the 41-CMA total.",
        "C01 by tier", "fact_cma_market_scale_snapshot", U_LOC, P_1097, "tiers", round(tm[1]), round(tm[3]), "administrative count",
        "tiers were defined by evidence coverage, not size", "FACT: DATA", "tier and scale are confounded; test scale within the 35-CMA set")
    m = mo.set_index("cma_code_sgc2021")
    add("Recent momentum", f"Across 35 CMAs the 12-month mean monthly opening rate ranges {m.mean_opening_rate_12m.min():.2%} to {m.mean_opening_rate_12m.max():.2%} (median {m.mean_opening_rate_12m.median():.2%}); closure rate median {m.mean_closure_rate_12m.median():.2%}.",
        "C03, C04", "fact_cma_momentum_monthly", U_RATE, P_RECENT, "35 CMAs", round(m.mean_opening_rate_12m.median(), 5), round(m.mean_closure_rate_12m.median(), 5),
        f"{int(m.e_flagged_observations_12m.sum())} E-flagged observations in window", MOM_LABEL, "FACT: DATA", "rates are comparable across CMAs; label as all-employer context")
    pos = int((m.mean_net_formation_rate_12m > 0).sum())
    add("Recent momentum", f"{pos} of 35 CMAs had a positive 12-month mean net-formation rate; the median was {m.mean_net_formation_rate_12m.median():.3%} per month.",
        "C05", "fact_cma_momentum_monthly", U_RATE, P_RECENT, "35 CMAs", pos, 35, "complete 12/12 months", MOM_LABEL, "FACT: DATA",
        "net formation is small relative to gross flows; small differences may be noise")
    inc = int((m.direction_net_formation_rate == "increase").sum())
    ex = int(m.change_net_formation_rate_exceeds_2se_heuristic.fillna(False).astype(bool).sum())
    add("Momentum direction", f"Net-formation rate rose versus the prior 12 months in {inc} of 35 CMAs; {ex} of 35 changes exceed the 2-SE noise heuristic.",
        "C08", "fact_cma_momentum_monthly", "rate difference", f"{P_RECENT} vs {P_PRIOR}", "35 CMAs", inc, ex, "heuristic ignores autocorrelation",
        "month-to-month noise; regression to the mean", "FACT: DATA", "treat acceleration as a candidate with a stability check, not a standalone signal")
    tp = int((m.trend_net_formation_rate_36m_per_year > 0).sum())
    add("Momentum direction", f"The 36-month linear trend in net-formation rate is positive in {tp} of 35 CMAs.", "C09", "fact_cma_momentum_monthly", "rate change per year",
        P_W36, "35 CMAs", tp, 35, "complete windows", "linear fit; not a forecast", "FACT: DATA", "compare C08 and C09 for consistency before choosing one")
    gap = m.net_formation_minus_active_change
    add("Recent momentum", f"Summed 12-month net formation differs from the change in active enterprises (median absolute gap {gap.abs().median():,.0f} enterprises).",
        "C06 vs C07", "fact_cma_momentum_monthly", "enterprise", P_RECENT, "35 CMAs", round(gap.abs().median()), None, "both from published SA series",
        "reopenings, exits and separate seasonal adjustment are not in openings - closures", "INFERENCE",
        "C06 and C07 are different quantities; do not use one to validate the other")
    vol = m.volatility_net_formation_rate_12m_sd
    cr = corr[(corr.indicator_a == "C01") & (corr.indicator_b == "C10")]
    add("Stability", f"Month-to-month volatility of net-formation rate ranges {vol.min():.3%} to {vol.max():.3%} (12-month SD); highest in {lab[vol.idxmax()]}.",
        "C10", "fact_cma_momentum_monthly", "rate SD", P_RECENT, lab[vol.idxmax()], round(vol.max(), 5), round(vol.median(), 5), "complete windows",
        "smaller CMAs have noisier rates", "FACT: DATA",
        f"volatility vs scale Spearman rho = {cr.spearman_rho.iloc[0]:.2f}; control for size if used" if len(cr) else "check scale dependence")
    add("Evidence quality", f"All 35 eligible CMAs have 12 of 12 usable months for every core momentum series; {int(m.e_flagged_observations_12m.sum())} E-flagged and {int(m.unavailable_observations_12m.sum())} unavailable observations in the recent window.",
        "C19", "fact_cma_momentum_monthly", "count of observations", P_RECENT, "35 CMAs", int(m.e_flagged_observations_12m.sum()),
        int(m.unavailable_observations_12m.sum()), "E flags retained with caution", "pandemic months are outside the windows", "FACT: DATA",
        "momentum coverage is sufficient for the 35-CMA decision set")
    sb = t["small"]
    c14 = int(sb[(sb.size_band_key == "1-4") & sb.flow_rates_complete_12m].shape[0])
    c519 = int(sb[(sb.size_band_key == "5-19") & sb.flow_rates_complete_12m].shape[0])
    a519 = int(sb[(sb.size_band_key == "5-19") & sb.active_complete_12m].shape[0])
    add("Small-business momentum", f"Complete 12-month opening and closing rates exist for the 1-4 band in {c14} CMAs and for the 5-19 band in {c519}; active counts for 5-19 are complete in {a519}.",
        "C20, C21", "fact_cma_momentum_monthly", U_RATE, P_RECENT, "35 CMAs x 2 bands", c14, c519, "bands kept separate",
        "bands are not an SME total and say nothing about 20-499", "FACT: DATA", "1-4 rate is testable on 35 CMAs; 5-19 would bias comparisons (13 missing)")
    top_counts = ind.largest_sector_label.value_counts()
    add("Industry composition", f"The largest SME sector is '{top_counts.index[0]}' in {top_counts.iloc[0]} of 41 CMAs.", "largest sector share", "fact_cma_sector_composition_snapshot",
        "share of locations", P_1097, "41 CMAs", int(top_counts.iloc[0]), 41, "administrative count", "shares use the classified 19-sector denominator", "FACT: DATA",
        "sector mix is broadly similar; specialization is the distinguishing signal")
    i = ind.set_index("cma_code_sgc2021")
    add("Industry composition", f"Sector HHI ranges {i.sector_hhi.min():.3f} ({lab[i.sector_hhi.idxmin()]}, most diversified) to {i.sector_hhi.max():.3f} ({lab[i.sector_hhi.idxmax()]}, most concentrated); effective number of sectors {i.effective_number_of_sectors.min():.1f} to {i.effective_number_of_sectors.max():.1f}.",
        "C11", "fact_cma_sector_composition_snapshot", "index", P_1097, "41 CMAs", round(i.sector_hhi.max(), 4), round(i.sector_hhi.min(), 4), "administrative count",
        "narrow range; direction of preference not established", "FACT: DATA", "Phase I must decide whether diversification is desirable")
    sd = t["sector_detail"]
    ov = sd[sd.specialization_flag.str.startswith("over")]
    lq = ov.loc[ov.location_quotient_vs_41_cma_universe.idxmax()] if len(ov) else None
    add("Industry composition", f"{len(ov)} CMA x sector cells are materially over-represented (LQ >= {LQ_HIGH}, >= {LQ_MIN_COUNT} locations, |z| >= {LQ_MIN_Z:.0f})"
        + (f"; the largest is {lq.industry_label} in {lq.cma_label_sgc2021} (LQ {lq.location_quotient_vs_41_cma_universe:.2f}, {int(lq.sme_locations):,} locations)." if lq is not None else "."),
        "C12 location quotient", "fact_cma_sector_composition_snapshot", "ratio of shares", P_1097, lq.cma_label_sgc2021 if lq is not None else "",
        round(float(lq.location_quotient_vs_41_cma_universe), 3) if lq is not None else None, 1.0, "administrative count; minimum-count rule applied",
        "benchmark is the 41-CMA universe, not Canada", "FACT: DATA", "specialization informs sector targeting, not market comparison")
    nsmall = int(sd[(sd.sector_role == "business_sector") & sd.small_count_flag].shape[0])
    add("Industry composition", f"{nsmall} of {41 * 19} CMA x business-sector cells have fewer than {SMALL_COUNT} SME locations; their shares and LQs are unstable.",
        "small-count cells", "fact_cma_sector_composition_snapshot", U_LOC, P_1097, "mostly smaller CMAs", nsmall, 41 * 19, "administrative count",
        "small counts inflate LQ swings", "FACT: DATA", "require minimum counts for any sector-level indicator")
    s = size.set_index("cma_code_sgc2021")
    add("Size composition", f"1-4 employee locations are {s['share_1-4'].median():.1%} of SME locations at the median CMA (range {s['share_1-4'].min():.1%} to {s['share_1-4'].max():.1%}); 100-499 is {s['share_100-499'].median():.2%}.",
        "C13, C16", "fact_cma_size_composition_snapshot", "share of locations", P_1097, "41 CMAs", round(s["share_1-4"].median(), 4), round(s["share_100-499"].median(), 4),
        "administrative count", "location-based, not enterprise size", "FACT: DATA", "size mix varies modestly; direction must be set by product strategy")
    dsp = s[s.distinct_size_profile_flag]
    add("Size composition", f"{len(dsp)} CMAs have a size-band share at least 2 SD from the 41-CMA mean: {', '.join(f'{lab[c]} ({b})' for c, b in dsp.distinct_size_profile_bands.items())}.",
        "size-share z-scores", "fact_cma_size_composition_snapshot", "share of locations", P_1097, ";".join(dsp.index), len(dsp), 41, "administrative count",
        "z-scores on 41 observations; descriptive only", "FACT: DATA", "flag distinct profiles; do not treat as preference")
    add("Size composition", f"The smallest CMA-level 100-499 count is {int(s['locations_100-499'].min())} locations ({lab[s['locations_100-499'].idxmin()]}); at CMA x sector x 100-499 only {facts['b_band_100_499_pct_ge10']}% of cells have 10+ locations (Phase F).",
        "100-499 counts", "fact_cma_size_composition_snapshot", U_LOC, P_1097, lab[s["locations_100-499"].idxmin()], int(s["locations_100-499"].min()),
        facts["b_band_100_499_pct_ge10"], "administrative count", "the analytical views do not hold the CMA x sector x size cross", "FACT: DATA",
        "100-499 is usable at CMA level only")
    for mid, name in (("ISED_T01_M001", "external-financing request rate"), ("ISED_T03_M001", "debt request rate")):
        d = isd[(isd.measure_id == mid)]
        natv = float(d[d.segment_dimension == "national"].value_published.iloc[0])
        c = d[d.segment_dimension == "cma"]
        nd = int(c.distinguishable_from_national.fillna(False).astype(bool).sum())
        pw = t["ised_facts"]["pairwise"][mid]
        add("Financing context", f"The national {name} is {natv:.1f}%; {nd} of {int(c.usable_in_comparison.sum())} usable CMA estimates are distinguishable from it, and {pw['distinguishable']} of {pw['pairs']} CMA pairs.",
            f"ISED {mid}", "fact_ised_financing_context", "survey estimate (% of SMEs)", P_ISED, "24 Tier 1 CMAs", nd, pw["pairs"],
            f"{int(c.caution_flag.sum())} E, {int(c.excluded_quality_f.sum())} F at CMA level", "sampling error; segment-vs-national test is conservative",
            "FACT: DATA", "CMA survey differences are mostly not distinguishable: keep ISED as context")
    ap = isd[(isd.measure_id.isin([f"ISED_T04_M00{k}" for k in range(6, 10)])) & (isd.segment_dimension == "cma")]
    add("Financing context", f"Product-level approval rates at CMA level include {int(ap.excluded_quality_f.sum())} F and {int(ap.caution_flag.sum())} E estimates out of {len(ap)}.",
        "ISED Table 4 approval rates", "fact_ised_financing_context", "survey estimate (% of requesters)", P_ISED, "24 Tier 1 CMAs", int(ap.excluded_quality_f.sum()),
        len(ap), "F excluded; E caution", "small requester samples", "FACT: DATA", "reject CMA-level product approval rates as indicators")
    add("Evidence coverage", "Six Tier 3 CMAs have scale and composition evidence but no 0722 momentum history; they stay visible and are excluded from momentum comparisons.",
        "evidence tier", "dim_cma", "CMA", "not applicable", "Tier 3 (6 CMAs)", 6, 41, "not applicable", TIER3_LABEL, "DECISION",
        "Phase I must define how Tier 3 appears (for example, a separate scale-only view)")
    for a, b, text in (("C01", "C05", "scale vs net-formation rate"), ("C03", "C05", "opening rate vs net-formation rate"), ("C04", "C05", "closure rate vs net-formation rate"),
                       ("C05", "C08", "recent net-formation level vs change"), ("C01", "C11", "scale vs sector concentration"), ("C01", "C13", "scale vs micro-location share"),
                       ("C22", "C23", "external-financing vs debt request rate")):
        r = corr[(corr.indicator_a == a) & (corr.indicator_b == b)]
        if len(r):
            r = r.iloc[0]
            add("Redundancy", f"{text}: Spearman rho = {r.spearman_rho:.2f} (n = {r.n}).", f"{a} vs {b}", "see candidate register",
                f"{r.statistical_unit_a} vs {r.statistical_unit_b}", f"{r.period_a} | {r.period_b}", r.row_basis, round(r.spearman_rho, 3), r.n,
                r.association_type, r.structural_note or "association, not causation", "FACT: DATA",
                "review for redundancy in Phase I" if abs(r.spearman_rho) >= 0.8 else "not redundant at the 0.8 threshold")
    ext = dist[dist.extreme_observations_tukey != ""]
    add("Outliers", f"{len(ext)} of {len(dist)} quantitative candidates have Tukey extremes; none are removed or winsorized.", "Tukey 1.5 x IQR fences",
        "indicator_distribution_summary", "various", "various", ";".join(ext.indicator_id), len(ext), len(dist), "original values preserved",
        "Tukey fences on 24-41 observations are indicative only", "FACT: DATA", "Phase I to choose robust scaling; do not delete markets")
    return pd.DataFrame(F)


# ---------------------------------------------------
# 6. Main
# ---------------------------------------------------

def main():
    start = time.time()
    run_ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    hashed_before, statted_before = snapshot()
    man, ffacts = upstream()
    if not all(gates.values()):
        return finish(start, run_ts, {}, published=False)

    banner("2. ANALYSIS")
    views = load_views()
    t = analyze(views)
    f = indicator_frame(t)
    dist = distributions(t, f)
    corr = correlations(f)
    fnd = findings(t, f, dist, corr, ffacts)
    reg = pd.DataFrame([dict(zip(CAND_COLS, c)) for c in CANDIDATES])
    reg.insert(2, "business_rationale", reg.analytical_role.map(RATIONALE))
    cov = dist.set_index("indicator_id")
    reg["coverage"] = reg.indicator_id.map(lambda i: f"{int(cov.loc[i, 'count'])} of {int(cov.loc[i, 'eligible_cmas'])} eligible CMAs" if i in cov.index else
                                           {"C25": "24 Tier 1 CMAs (F/E-heavy)", "C26": "national + 4 size bands", "C27": "8 of 35 CMAs", "C28": "41 CMAs"}[i])
    reg["distribution_shape"] = reg.indicator_id.map(lambda i: (f"skew {cov.loc[i, 'skewness']:.2f}" + (f"; log10 skew {cov.loc[i, 'skewness_log10']:.2f}"
                                                               if pd.notna(cov.loc[i, 'skewness_log10']) else "")) if i in cov.index else "not assessed")
    red = {}
    for r in corr.itertuples():
        for a, b in ((r.indicator_a, r.indicator_b), (r.indicator_b, r.indicator_a)):
            if abs(r.spearman_rho) >= 0.5:
                red.setdefault(a, []).append(f"{b} (rho {r.spearman_rho:.2f}{'; ' + r.structural_note if r.structural_note else ''})")
    reg["duplication_or_correlation_risk"] = reg.indicator_id.map(lambda i: "; ".join(red.get(i, [])) or "no |rho| >= 0.5 with other candidates")
    reg["source_view"] = reg.authoritative_source_view
    order = ["indicator_id", "indicator_name", "business_rationale", "analytical_role", "authoritative_source_view", "statistical_unit", "formula", "grain",
             "period", "eligible_tiers", "coverage", "directionality", "missing_value_treatment", "quality_treatment", "outlier_sensitivity", "distribution_shape",
             "possible_transformation", "duplication_or_correlation_risk", "interpretation_risk", "recommended_status", "profile_column", "source_view"]
    reg = reg[order]

    outputs = {"cma_evidence_profile.csv": t["evidence"], "cma_market_scale_profile.csv": t["scale"], "cma_momentum_profile.csv": t["momentum"],
               "small_business_momentum_coverage.csv": t["small"], "cma_industry_profile.csv": t["industry"], "cma_sector_detail.csv": t["sector_detail"],
               "cma_size_profile.csv": t["size"], "ised_financing_context_profile.csv": t["ised"], "indicator_distribution_summary.csv": dist,
               "indicator_correlation_matrix.csv": corr, "candidate_indicator_register.csv": reg, "descriptive_findings.csv": fnd,
               "cma_momentum_monthly_series.parquet": t["series"]}

    banner("3. FIGURES")
    specs = figure_specs(t, f)
    figs, fig_reg = {}, []
    for sp in specs:
        png, meta = render(sp)
        png2, _ = render(sp)
        meta["reproducible_render"] = hashlib.sha256(png).hexdigest() == hashlib.sha256(png2).hexdigest()
        figs[sp[0]] = png
        fig_reg.append(meta)
        print(f"  rendered {sp[0]}")
    fig_reg = pd.DataFrame(fig_reg)

    banner("4. VALIDATION GATES")
    mom, sca, ind, siz, isd, ev, ser = t["momentum"], t["scale"], t["industry"], t["size"], t["ised"], t["evidence"], t["series"]
    dim = views["dim_cma"]
    elig = set(dim[dim.momentum_comparison_eligible].cma_code_sgc2021)
    tier3 = set(dim[dim.tier.astype(int) == 3].cma_code_sgc2021)
    mart_prefix = f"{WAREHOUSE.relative_to(ROOT).as_posix()}::analytics."
    bad_reads = [p for p in READS if p.startswith(("data/raw/", "data/cache/", "data/processed/"))]
    gate("reads_only_dbt_marts_and_validated_summaries", not bad_reads and any(p.startswith(mart_prefix) for p in READS)
         and all(p.startswith(mart_prefix) or p.startswith("data/validation/") for p in READS),
         f"{len({p for p in READS if p.startswith(mart_prefix)})} dbt marts and {len({p for p in READS if p.startswith('data/validation/')})} validated summaries; "
         "none from raw files, caches or baseline Parquet")
    for name, df in outputs.items():
        ok = all(c in df.columns for c in ("source_view", "statistical_unit", "period")) and df[["source_view", "statistical_unit", "period"]].notna().all().all()
        gate(f"output_declares_source_unit_period__{name}", ok)
    gate("momentum_outputs_contain_exactly_the_35_eligible_cmas", set(mom.cma_code_sgc2021) == elig and len(mom) == 35 and set(ser.cma_code_sgc2021) == elig
         and set(t["small"].cma_code_sgc2021) == elig and len(elig) == 35 and set(dim[dim.tier.astype(int).isin([1, 2])].cma_code_sgc2021) == elig, f"{len(mom)} rows")
    t3 = ev[ev.cma_code_sgc2021.isin(tier3)]
    gate("tier3_has_no_momentum_values", not (set(views["momentum_cma_codes"]) & tier3) and t3[["momentum_e_flagged_12m", "momentum_unavailable_12m"]].isna().all().all()
         and (t3.primary_opportunity_analysis_eligibility == TIER3_LABEL).all() and len(t3) == 6 and f.loc[list(tier3), "mean_net_formation_rate_12m"].isna().all(), "6 Tier 3 CMAs")
    w = ser.pivot(index=["cma_code_sgc2021", "ref_month"], columns="measure", values="value").reset_index().sort_values(["cma_code_sgc2021", "ref_month"])
    w["active_prev"] = w.groupby("cma_code_sgc2021").active.shift(1)
    w["prev_month"] = w.groupby("cma_code_sgc2021").ref_month.shift(1)
    w["expected_prev"] = (pd.PeriodIndex(w.ref_month, freq="M") - 1).astype(str)
    w.loc[w.prev_month != w.expected_prev, "active_prev"] = np.nan
    diffs = []
    for m_, num in (("opening_rate", w.openings), ("closure_rate", w.closures), ("net_formation_rate", w.openings - w.closures)):
        exp = num / w.active_prev
        both = exp.notna() | w[m_].notna()
        diffs.append(int(((exp - w[m_]).abs() > 1e-12).sum() + (exp.isna() != w[m_].isna())[both].sum()))
    gate("momentum_rates_use_0722_active_t_minus_1_denominators", sum(diffs) == 0 and (ser.source_id == "StatCan 33-10-0722-01").all(),
         f"{len(w):,} CMA-months recomputed from 0722 published values; {sum(diffs)} differences")
    gate("all_size_momentum_labelled_all_employer_context", (mom.scope_label == MOM_LABEL).all() and (ser.scope_label == MOM_LABEL).all()
         and not mom.scope_label.str.contains("SME-specific").any())
    gate("recent_and_prior_windows_contain_expected_months", RECENT == [f"{y}-{m:02d}" for y, m in [(2025, k) for k in range(6, 13)] + [(2026, k) for k in range(1, 6)]]
         and PRIOR == [f"{y}-{m:02d}" for y, m in [(2024, k) for k in range(6, 13)] + [(2025, k) for k in range(1, 6)]]
         and len(set(RECENT) & set(PRIOR)) == 0 and ser[ser.in_recent_12m].ref_month.nunique() == 12 and ser[ser.in_prior_12m].ref_month.nunique() == 12,
         "2025-06..2026-05 and 2024-06..2025-05")
    gate("window_36m_defined_consistently", len(W36) == 36 and W36[-1] == "2026-05" and set(RECENT) <= set(W36) and set(PRIOR) <= set(W36)
         and ser[ser.in_trailing_36m].ref_month.nunique() == 36 and (mom.usable_months_net_formation_rate_36m == 36).all())
    pm = ser[ser.pandemic_window_flag]
    gate("pandemic_observations_flagged_and_retained", set(pm.ref_month) == set(PANDEMIC) and pm.groupby("cma_code_sgc2021").ref_month.nunique().eq(22).all()
         and pm.cma_code_sgc2021.nunique() == 35 and ser.ref_month.nunique() == 137 and not ser[ser.in_trailing_36m].pandemic_window_flag.any(),
         f"{len(pm):,} pandemic-window observations kept in the monthly series; none in analysis windows")
    gate("1097_measures_location_based", all((df.statistical_unit == U_LOC).all() for df in (sca, ind, siz, t["sector_detail"])))
    gate("sme_location_totals_exclude_500_plus", ((sca.sme_employer_locations + sca.employer_locations_500_plus) == sca.all_employer_locations).all()
         and (siz[[f"locations_{b}" for b in SIZE_BANDS]].sum(axis=1) == sca.set_index("cma_code_sgc2021").loc[siz.cma_code_sgc2021, "sme_employer_locations"].values).all())
    sdt = t["sector_detail"]
    bsd = sdt[sdt.sector_role == "business_sector"]
    gate("sector_shares_use_classified_19_sector_denominator", bsd.groupby("cma_code_sgc2021").naics_sector.nunique().eq(19).all()
         and (bsd.groupby("cma_code_sgc2021").sme_locations.sum() == bsd.groupby("cma_code_sgc2021").share_denominator.first()).all()
         and not bsd.naics_sector.isin(["91", "UNCLASSIFIED"]).any())
    gate("sector_shares_reconcile", (bsd.share - bsd.share_recomputed).abs().max() < 1e-12 and (bsd.groupby("cma_code_sgc2021").share.sum() - 1).abs().max() < 1e-12,
         f"max deviation {(bsd.share - bsd.share_recomputed).abs().max():.1e}")
    uni = bsd.groupby("naics_sector").sme_locations.sum() / bsd.sme_locations.sum()
    lq2 = bsd.share_recomputed / bsd.naics_sector.map(uni)
    gate("location_quotients_use_only_1097_values", (lq2 - bsd.location_quotient_vs_41_cma_universe).abs().max() < 1e-12
         and (bsd.source_view == "fact_cma_sector_composition_snapshot").all() and abs((uni).sum() - 1) < 1e-12)
    ns = sdt[~sdt.naics_sector.isin(bsd.naics_sector.unique())]
    gate("unclassified_not_allocated", ns.share_recomputed.isna().all() and ns.location_quotient_vs_41_cma_universe.isna().all()
         and (ns.specialization_flag == "not assessed (shown separately)").all() and len(ns) == 82)
    gate("sme_size_shares_reconcile", max((siz[f"share_{b}"] - siz[f"share_{b}_recomputed"]).abs().max() for b in SIZE_BANDS) < 1e-12
         and (siz[[f"share_{b}" for b in SIZE_BANDS]].sum(axis=1) - 1).abs().max() < 1e-12)
    gate("ised_F_excluded_from_usable_comparisons", isd[isd.excluded_quality_f].value_for_comparison.isna().all()
         and isd[isd.excluded_quality_f].distinguishable_from_national.isna().all() and not isd[isd.excluded_quality_f].usable_in_comparison.any()
         and not (f[[c for c in f.columns if c.startswith("ised_") and not c.endswith("__E")]].notna().values & False).any(),
         f"{int(isd.excluded_quality_f.sum())} F estimates")
    gate("ised_E_values_carry_caution", (isd[isd.quality_code == "E"].caution_flag).all() and (isd[isd.caution_flag].quality_code == "E").all(),
         f"{int(isd.caution_flag.sum())} E estimates")
    gate("ised_dotdot_never_zero", isd[isd.dotdot_flag].value_published.isna().all() and isd[isd.dotdot_flag].value_for_comparison.isna().all())
    cnt_cols = [c for df in outputs.values() for c in df.columns if re.search(r"ised.*count|count.*ised", c, re.I)]
    gate("ised_percentages_not_converted_to_counts", not cnt_cols and (isd[isd.unit.str.contains("percent")].value_for_comparison.dropna() <= 100).all()
         and set(isd.estimate_type) >= {"percentage_of_universe"})
    cma_keys = set(isd[isd.segment_dimension == "cma"].segment_key)
    tier1 = set(dim[dim.tier.astype(int) == 1].cma_code_sgc2021)
    gate("no_regional_ised_value_copied_to_a_cma", cma_keys == tier1 and not (set(isd[isd.segment_dimension == "region"].segment_key) & set(dim.cma_code_sgc2021))
         and ev[~ev.cma_code_sgc2021.isin(tier1)][["ised_cma_estimates", "ised_cma_usable_estimates"]].isna().all().all()
         and f.loc[[c for c in f.index if c not in tier1], ["ised_I01_request_rate", "ised_I02_debt_request_rate"]].isna().all().all(),
         "Tier 2 and Tier 3 ISED CMA fields are null")
    gate("every_candidate_has_source_formula_direction_quality", reg[["authoritative_source_view", "formula", "directionality", "quality_treatment"]].map(
        lambda x: isinstance(x, str) and len(x) > 0).all().all() and reg.indicator_id.is_unique and reg.recommended_status.isin(
        ["retain for Phase I testing", "supporting context", "reject", "unresolved"]).all(), f"{len(reg)} candidates")
    allcols = [c for df in outputs.values() for c in df.columns] + list(fig_reg.columns)
    gate("no_candidate_has_a_weight", not any(re.search(r"weight", c, re.I) for c in allcols))
    gate("no_composite_score_exists", not any(re.search(r"score", c, re.I) for c in allcols))
    gate("no_final_market_ranking_column_exists", not any(re.search(r"rank", c, re.I) for c in allcols))
    cma_outputs = [df for n, df in outputs.items() if "cma_code_sgc2021" in df.columns]
    hits = [v_ for df in cma_outputs for c in df.columns if df[c].dtype == object for v_ in df[c].dropna().astype(str) if FORBIDDEN_VALUES.search(v_)]
    hits += [c for df in cma_outputs for c in df.columns if FORBIDDEN_COLS.search(c)]
    hits += [x for x in fig_reg.title.tolist() + fig_reg.x_label.tolist() + fig_reg.y_label.tolist() if re.search(r"priorit|recommend|\brank", x, re.I)]
    gate("no_market_labelled_recommended_or_priority", not hits, f"{len(hits)} hits")
    gate("findings_contain_evidence_and_limitations", fnd[["finding_statement", "evidence_measure", "source_view", "limitation", "evidence_label"]].map(
        lambda x: isinstance(x, str) and len(x) > 0).all().all() and fnd.evidence_label.isin(["FACT: DATA", "INFERENCE", "DECISION"]).all()
        and not fnd.finding_statement.str.contains(r"should prioriti|recommend|target market", case=False).any(), f"{len(fnd)} findings")
    gate("figure_labels_include_units_and_periods", len(fig_reg) == 7 and all(all(p_.strip() in r.title for p_ in r.period.split(";")) for r in fig_reg.itertuples())
         and all(all(u.strip() in (r.x_label + " " + r.y_label) for u in r.unit.split(";")) or r.unit == "availability (categorical)" for r in fig_reg.itertuples())
         and all(len(r.source_note) > 20 for r in fig_reg.itertuples()))
    gate("figures_render_reproducibly", fig_reg.reproducible_render.all())
    t2 = analyze(load_views())
    f2 = indicator_frame(t2)
    same = all(frame_hash(a) == frame_hash(b) for a, b in (
        (t["momentum"], t2["momentum"]), (t["scale"], t2["scale"]), (t["industry"], t2["industry"]), (t["size"], t2["size"]),
        (t["ised"], t2["ised"]), (t["evidence"], t2["evidence"]), (t["small"], t2["small"]), (t["sector_detail"], t2["sector_detail"]),
        (dist, distributions(t2, f2)), (corr, correlations(f2))))
    gate("analysis_is_reproducible_in_process", same, "all tables recomputed from the views and hashed")
    required_ok = all(gates.values())

    published = False
    tmp_o = OUT.parent / f".market_exploration_build_{os.getpid()}"
    tmp_f = FIG.parent / f".phase_h_build_{os.getpid()}"
    for d_ in (tmp_o, tmp_f):
        shutil.rmtree(d_, ignore_errors=True)
        d_.mkdir(parents=True)
    for name, df in outputs.items():
        if name.endswith(".parquet"):
            df.to_parquet(tmp_o / name, index=False)
        else:
            df.to_csv(tmp_o / name, index=False, encoding="utf-8", lineterminator="\n", float_format="%.10g")
    fig_reg.drop(columns="reproducible_render").assign(source_view="see source_note", statistical_unit=fig_reg.unit, period=fig_reg.period).to_csv(
        tmp_o / "figure_register.csv", index=False, encoding="utf-8", lineterminator="\n")
    for fid, png in figs.items():
        (tmp_f / f"{fid}.png").write_bytes(png)
    if required_ok:
        banner("5. PUBLISH")
        OUT.mkdir(parents=True, exist_ok=True)
        FIG.mkdir(parents=True, exist_ok=True)
        for p in tmp_o.iterdir():
            os.replace(p, OUT / p.name)
        for p in tmp_f.iterdir():
            os.replace(p, FIG / p.name)
        published = True
    else:
        print("  required gates failed: outputs NOT published")
    shutil.rmtree(tmp_o, ignore_errors=True)
    shutil.rmtree(tmp_f, ignore_errors=True)

    m = mom.set_index("cma_code_sgc2021")
    sf = t["scale_facts"]
    facts = {
        "sme_total_41": sf["sme_total_41"], "sme_median": round(sf["sme_median"]), "sme_max": sf["sme_max"], "sme_min": sf["sme_min"],
        "top3_share_pct": round(sf["top3_share"] * 100, 1), "top5_share_pct": round(sf["top5_share"] * 100, 1), "cmas_for_half": sf["cmas_for_half"],
        "gini_across_cmas": round(sf["gini_across_cmas"], 3), "tier1_share_pct": round(sf["tier_share"][1] * 100, 1),
        "tier_median": {k: round(v_) for k, v_ in sf["tier_median"].items()},
        "unc_median_pct": round(sca.unclassified_share_of_sme_locations.median() * 100, 1), "unc_max_pct": round(sca.unclassified_share_of_sme_locations.max() * 100, 1),
        "pa_median_pct": round(sca.public_administration_share_of_sme_locations.median() * 100, 2),
        "pa_max_pct": round(sca.public_administration_share_of_sme_locations.max() * 100, 2),
        "open_median_pct": round(m.mean_opening_rate_12m.median() * 100, 2), "close_median_pct": round(m.mean_closure_rate_12m.median() * 100, 2),
        "nfr_median_pct": round(m.mean_net_formation_rate_12m.median() * 100, 3), "nfr_positive": int((m.mean_net_formation_rate_12m > 0).sum()),
        "nfr_increase": int((m.direction_net_formation_rate == "increase").sum()),
        "nfr_exceeds_2se": int(m.change_net_formation_rate_exceeds_2se_heuristic.fillna(False).astype(bool).sum()),
        "trend_positive": int((m.trend_net_formation_rate_36m_per_year > 0).sum()),
        "e_recent": int(m.e_flagged_observations_12m.sum()), "unavail_recent": int(m.unavailable_observations_12m.sum()),
        "sb_flows_1_4": int(t["small"][(t["small"].size_band_key == "1-4") & t["small"].flow_rates_complete_12m].shape[0]),
        "sb_flows_5_19": int(t["small"][(t["small"].size_band_key == "5-19") & t["small"].flow_rates_complete_12m].shape[0]),
        "sb_active_5_19": int(t["small"][(t["small"].size_band_key == "5-19") & t["small"].active_complete_12m].shape[0]),
        "hhi_min": round(ind.sector_hhi.min(), 3), "hhi_max": round(ind.sector_hhi.max(), 3),
        "over_cells": int((t["sector_detail"].specialization_flag == "over-represented vs 41-CMA universe").sum()),
        "under_cells": int((t["sector_detail"].specialization_flag == "under-represented vs 41-CMA universe").sum()),
        "small_cells": int(bsd.small_count_flag.sum()), "share14_median_pct": round(siz["share_1-4"].median() * 100, 1),
        "share100_median_pct": round(siz["share_100-499"].median() * 100, 2), "distinct_size": int(siz.distinct_size_profile_flag.sum()),
        "min_100_499": int(siz["locations_100-499"].min()), "ised_F": int(isd.excluded_quality_f.sum()), "ised_E": int(isd.caution_flag.sum()),
        "ised_rows": len(isd), "candidates": len(reg), "status_counts": reg.recommended_status.value_counts().to_dict(),
        "retained": reg[reg.recommended_status == "retain for Phase I testing"].indicator_id.tolist(),
        "context": reg[reg.recommended_status == "supporting context"].indicator_id.tolist(),
        "rejected": reg[reg.recommended_status == "reject"].indicator_id.tolist(), "unresolved": reg[reg.recommended_status == "unresolved"].indicator_id.tolist(),
        "findings": len(fnd), "figures": len(fig_reg), "pairs": len(corr), "high_redundancy_pairs": corr[corr.spearman_rho.abs() >= 0.8][["indicator_a", "indicator_b", "spearman_rho"]].round(3).values.tolist(),
        "ised_pairwise": t["ised_facts"]["pairwise"], "extreme_candidates": dist[dist.extreme_observations_tukey != ""].indicator_id.tolist(),
        "b_band_100_499_pct_ge10": ffacts["b_band_100_499_pct_ge10"]}
    finish_docs(facts, fnd, corr)
    hashed_after, statted_after = snapshot()
    changed = sorted(k for k in set(hashed_before) | set(hashed_after) if hashed_before.get(k) != hashed_after.get(k))
    gate("no_raw_or_processed_or_protected_input_changed", not changed and statted_before == statted_after,
         f"{len(hashed_after)} files hashed (processed, reference, validation, manifest); {len(statted_after)} raw/cache files checked by size and mtime (never opened)"
         + (f"; changed {changed}" if changed else ""))
    gate("analytical_views_still_match_manifest_hashes", all(sha256_file(ROOT / r.output_path) == r.file_sha256 for r in man.itertuples()))
    out_sha = {p.relative_to(ROOT).as_posix(): sha256_file(p) for d_ in (OUT, FIG) if d_.exists() for p in sorted(d_.iterdir()) if p.is_file() and p.suffix != ".json"}
    finish(start, run_ts, {"facts": facts, "doc_claims": DOC_CLAIMS, "published": published, "output_sha256": out_sha,
                           "reads": sorted(set(READS)),
                           "integrity_note": "raw and cache files verified by size and modification time only (rule 7: never opened)"}, published)


DOC_CLAIMS = {}


def finish_docs(facts, fnd, corr):
    banner("6. DOCUMENTATION")
    F = {k: (f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)) for k, v in facts.items() if not isinstance(v, (dict, list))}
    DOC_CLAIMS.update({
        "sme_total": f"**{F['sme_total_41']}** SME employer locations", "sme_median": f"median CMA has **{F['sme_median']}**",
        "top3": f"**{F['top3_share_pct']}%**", "top5": f"**{F['top5_share_pct']}%**", "half": f"**{F['cmas_for_half']}** CMAs account for half",
        "tier1_share": f"Tier 1 holds **{F['tier1_share_pct']}%**", "unc": f"**{F['unc_median_pct']}%** of SME locations at the median CMA",
        "pa": f"**{F['pa_median_pct']}%**", "open": f"median opening rate **{F['open_median_pct']}%**", "close": f"closure rate **{F['close_median_pct']}%**",
        "nfr": f"**{F['nfr_positive']}** of 35 CMAs", "inc": f"rose in **{F['nfr_increase']}**", "2se": f"**{F['nfr_exceeds_2se']}** exceed",
        "trend": f"positive in **{F['trend_positive']}**", "e": f"**{F['e_recent']}** E-flagged", "sb14": f"1–4 band in **{F['sb_flows_1_4']}** CMAs",
        "sb519": f"5–19 band in **{F['sb_flows_5_19']}**", "hhi": f"**{F['hhi_min']}** to **{F['hhi_max']}**", "over": f"**{F['over_cells']}** CMA × sector cells",
        "small": f"**{F['small_cells']}** of 779", "s14": f"**{F['share14_median_pct']}%**", "distinct": f"**{F['distinct_size']}** CMAs",
        "min100": f"**{F['min_100_499']}** locations", "iF": f"**{F['ised_F']}** F", "iE": f"**{F['ised_E']}** E", "cands": f"**{F['candidates']}** candidates",
        "findings": f"**{F['findings']}** findings", "pairs": f"**{F['pairs']}** indicator pairs",
    })
    for k in ("retained", "context", "rejected", "unresolved"):
        DOC_CLAIMS[f"list_{k}"] = ", ".join(facts[k])
    for mid, p in facts["ised_pairwise"].items():
        DOC_CLAIMS[f"pw_{mid}"] = f"{p['distinguishable']} of {p['pairs']}"
    doc = DOC05.read_text(encoding="utf-8") if DOC05.exists() else ""
    missing = {k: v for k, v in DOC_CLAIMS.items() if v not in doc}
    gate("documentation_claims_match_outputs", bool(doc) and not missing,
         ("NOT YET DOCUMENTED" if not doc else f"{len(DOC_CLAIMS) - len(missing)} of {len(DOC_CLAIMS)} claims") + (f"; missing {missing}" if doc and missing else ""))
    gate("phase_h_linked_from_analytical_layer_doc", "05_external_market_exploration.md" in DOC04.read_text(encoding="utf-8"))


def finish(start, run_ts, fields, published):
    failed = [k for k, v in gates.items() if not v]
    summary = {"phase": "H", "script_version": SCRIPT_VERSION, "run_timestamp_utc": run_ts, **fields, "gates": gates, "failed_gates": failed,
               "all_gates_pass": not failed, "elapsed_seconds": round(time.time() - start, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "phase_h_summary.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2, default=str, ensure_ascii=False)
    banner("PHASE H COMPLETE" if not failed else "PHASE H FAILED")
    print(f"Gates: {len(gates) - len(failed)} of {len(gates)} pass; outputs published: {published}")
    for k in failed:
        print(f"  FAILED: {k}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
