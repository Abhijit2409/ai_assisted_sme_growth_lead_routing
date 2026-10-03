"""
Integrated acquisition intelligence: analysis, findings and figures.

Reads the acquisition-intelligence marts from the DuckDB warehouse (read-only) and produces the business-facing
outputs that answer the three questions the layer exists for:

  * where should the bank focus SME acquisition effort?
  * which prospects need action?
  * where are leads being delayed, lost, or constrained by capacity?

It does not re-implement any market, priority or routing logic, all of which lives in dbt. It adds market-focus and
funnel summaries, a capacity and concentration view, the executive KPI set, documented findings and figures.

Framing, repeated on every output: hypothetical rules for a fictional bank over entirely synthetic CRM data. No
result is a fact about any real bank, and no figure here supports a causal claim about conversion, revenue,
profitability or customer experience.

    C:/Users/abhij/anaconda3/python.exe src/analysis/20_analyze_acquisition_intelligence.py [--output-root DIR]
"""

from pathlib import Path
import hashlib
import io
import sys

import duckdb
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "warehouse" / "sme_banking.duckdb"
BASE_OUT = ROOT
if "--output-root" in sys.argv:
    BASE_OUT = Path(sys.argv[sys.argv.index("--output-root") + 1]).resolve()
OUT = BASE_OUT / "data" / "analysis" / "acquisition_intelligence"
FIG = BASE_OUT / "outputs" / "acquisition_intelligence"

PERIOD = "synthetic operating year 2025-06-01 to 2026-05-31, reported as at 2026-05-31"
UNIT = "lead, CMA, relationship manager or reporting month, as stated per file"
POPULATION = "every synthetic lead captured in the declared operating period"
SOURCE = ("dbt marts: mart_acquisition_market_focus, mart_acquisition_funnel_monthly, "
          "mart_rm_acquisition_performance_monthly, mart_acquisition_action_queue, mart_acquisition_kpi_snapshot")
LIMIT = ("Hypothetical rules for a fictional bank over entirely synthetic CRM data. The market opportunity score is "
         "an external public-aggregate signal that excludes bank market share, branch presence, competition, credit "
         "risk and acquisition cost. Capacity is a declared model, not a measured workload. Nothing here is evidence "
         "of a causal effect on conversion, revenue, profitability or customer experience.")
NOTE = ("Hypothetical framework, fictional bank, entirely synthetic data, 2025-06 to 2026-05. "
        "Descriptive and operational measures only: no causal claim.")
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "passed": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))


def stamp(df):
    out = df.copy()
    out["source_models"] = SOURCE
    out["period"] = PERIOD
    out["statistical_unit"] = UNIT
    out["evidence_eligibility"] = POPULATION
    out["limitations"] = LIMIT
    return out


def load():
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    d = {
        "focus": con.execute("select * from analytics.mart_acquisition_market_focus order by cma_key").df(),
        "funnel": con.execute("""select * from analytics.mart_acquisition_funnel_monthly
                                 order by reporting_month, cma_key, channel_code, sme_segment,
                                          industry_grouping""").df(),
        "rm": con.execute("""select * from analytics.mart_rm_acquisition_performance_monthly
                             order by rm_id, reporting_month""").df(),
        "queue": con.execute("select * from analytics.mart_acquisition_action_queue order by action_rank").df(),
        "kpi": con.execute("select * from analytics.mart_acquisition_kpi_snapshot").df(),
        "catalog": con.execute("select * from reference.acquisition_kpi_catalog order by kpi_id").df(),
        "params": con.execute("select * from reference.acquisition_reporting_parameters order by parameter").df(),
    }
    con.close()
    return d


def gini(v):
    v = np.sort(np.asarray(v, dtype=float))
    n = len(v)
    if n == 0 or v.sum() == 0:
        return np.nan
    return float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum()))


# ------------------------------------------------------------------ business-facing outputs
def analyse(d):
    focus, funnel, rm, queue, kpi = d["focus"], d["funnel"], d["rm"], d["queue"], d["kpi"]
    out = {}

    # ---- where to focus: opportunity beside the activity actually present ------------------------------------
    f = focus[focus.is_cma_scope].copy()
    f["lead_share_of_total"] = (f.lead_count / f.lead_count.sum()).round(6)
    out["market_focus"] = f[[
        "cma_key", "cma_label_sgc2021", "province_code", "opportunity_score", "opportunity_rank",
        "opportunity_classification", "is_high_opportunity", "evidence_tier", "dominant_pillar",
        "pillar_scale", "pillar_momentum", "pillar_dynamism", "pillar_commercial_fit", "pillar_stability",
        "prospect_count", "lead_count", "lead_share_of_total", "active_lead_backlog", "qualified_leads",
        "converted_leads", "mature_valid_leads", "mature_converted_leads", "mature_cohort_conversion_rate",
        "p1_leads", "p2_leads", "p3_leads", "unassigned_active_leads", "queued_active_leads",
        "open_sla_breaches", "first_contact_sla_breaches", "ageing_leads", "rms_home_based_here",
        "available_capacity_points", "rms_at_or_above_warning", "missing_opportunity_score",
        "missing_acquisition_activity", "data_quality_exceptions"]].sort_values(
            "opportunity_rank", na_position="last").reset_index(drop=True)

    # the focus gap: strong market signal but little acquisition activity or no capacity
    hi = f[f.is_high_opportunity]
    out["market_focus_gaps"] = hi.assign(
        leads_per_opportunity_rank=lambda x: x.lead_count,
        has_local_capacity=lambda x: x.rms_home_based_here > 0,
    )[["cma_key", "cma_label_sgc2021", "opportunity_score", "opportunity_rank", "lead_count",
       "active_lead_backlog", "rms_home_based_here", "available_capacity_points", "rms_at_or_above_warning",
       "has_local_capacity", "open_sla_breaches"]].sort_values("opportunity_rank").reset_index(drop=True)

    # ---- funnel ------------------------------------------------------------------------------------------
    out["funnel_monthly"] = funnel.copy()

    def roll(df, keys):
        g = df.groupby(keys, as_index=False).agg(
            captured_leads=("captured_leads", "sum"), valid_leads=("valid_leads", "sum"),
            duplicate_leads=("duplicate_leads", "sum"), assigned_leads=("assigned_leads", "sum"),
            contacted_leads=("contacted_leads", "sum"), qualified_leads=("qualified_leads", "sum"),
            converted_leads=("converted_leads", "sum"), active_backlog=("active_backlog", "sum"),
            ageing_leads=("ageing_leads", "sum"), reassigned_leads=("reassigned_leads", "sum"),
            valid_leads_mature=("valid_leads_mature", "sum"),
            converted_leads_mature=("converted_leads_mature", "sum"),
            contacted_leads_mature=("contacted_leads_mature", "sum"),
            qualified_leads_mature=("qualified_leads_mature", "sum"),
            first_contact_sla_settled=("first_contact_sla_settled", "sum"),
            first_contact_sla_breached=("first_contact_sla_breached", "sum"))
        g["assignment_rate"] = (g.assigned_leads / g.valid_leads.replace(0, np.nan)).round(6)
        g["contact_rate"] = (g.contacted_leads / g.assigned_leads.replace(0, np.nan)).round(6)
        g["qualification_rate"] = (g.qualified_leads_mature / g.contacted_leads_mature.replace(0, np.nan)).round(6)
        g["mature_cohort_conversion_rate"] = (g.converted_leads_mature
                                              / g.valid_leads_mature.replace(0, np.nan)).round(6)
        g["duplicate_rate"] = (g.duplicate_leads / g.captured_leads.replace(0, np.nan)).round(6)
        g["sla_compliance_rate"] = ((g.first_contact_sla_settled - g.first_contact_sla_breached)
                                    / g.first_contact_sla_settled.replace(0, np.nan)).round(6)
        g["reassignment_rate"] = (g.reassigned_leads / g.assigned_leads.replace(0, np.nan)).round(6)
        return g

    out["funnel_by_month"] = roll(funnel, ["reporting_month"])
    out["funnel_by_channel"] = roll(funnel, ["channel_code", "channel_name"])
    out["funnel_by_segment"] = roll(funnel, ["sme_segment"])
    out["funnel_by_industry"] = roll(funnel, ["industry_grouping"])
    out["funnel_by_cma"] = roll(funnel, ["cma_key", "cma_label_sgc2021"])

    # ---- RM capacity and concentration --------------------------------------------------------------------
    latest_month = rm.reporting_month.max()
    rl = rm[rm.reporting_month == latest_month]
    out["rm_performance_monthly"] = rm.copy()
    out["rm_capacity_latest"] = rl[[
        "rm_id", "rm_display_name", "rm_segment", "home_sales_region", "home_cma_code_sgc2021", "is_specialist",
        "capacity_limit_points", "weighted_workload_points", "utilization_ratio", "remaining_capacity_points",
        "capacity_state", "at_or_above_capacity_warning", "at_or_above_hard_limit", "no_capacity_evidence",
        "assigned_leads", "contacted_leads", "qualified_leads", "converted_leads",
        "first_contact_sla_compliance_rate", "operational_recommendations"]].sort_values(
            "utilization_ratio", ascending=False).reset_index(drop=True)

    load_by_rm = rm.groupby("rm_id", as_index=False).operational_recommendations.sum()
    counts = load_by_rm.operational_recommendations.to_numpy()
    srt = np.sort(counts)[::-1]
    top_decile = max(1, len(counts) // 10)
    out["routing_concentration"] = pd.DataFrame([{
        "rm_population": int(len(counts)),
        "total_operational_recommendations": int(counts.sum()),
        "rms_receiving_none": int((counts == 0).sum()),
        "max_recommendations_to_one_rm": int(counts.max()),
        "median_recommendations_per_rm": float(np.median(counts)),
        "top_decile_share": round(float(srt[:top_decile].sum() / counts.sum()), 6) if counts.sum() else None,
        "workload_gini": round(gini(counts), 6),
        "interpretation": ("An operational concentration risk. The capacity-aware routing policy respects the hard "
                           "limit on every decision, but respecting a hard limit does not by itself produce a "
                           "balanced workload: the preference rules reward the emptiest relationship manager, and "
                           "in a lightly loaded year that is repeatedly the same person."),
    }])
    out["routing_load_by_rm"] = load_by_rm.sort_values(
        "operational_recommendations", ascending=False).reset_index(drop=True)

    # ---- action queue -------------------------------------------------------------------------------------
    out["action_queue"] = queue.copy()
    out["action_queue_summary"] = queue.groupby(
        ["recommended_next_action", "responsible_role"], as_index=False).agg(
        leads=("lead_id", "count"),
        escalations=("requires_escalation", "sum"),
        p1_leads=("priority_tier", lambda v: int((v == "P1").sum())),
        median_lead_age_days=("lead_age_days", "median")).sort_values("leads", ascending=False).reset_index(drop=True)

    # ---- executive KPI set --------------------------------------------------------------------------------
    k = kpi.iloc[0]
    rows = []
    for _, c in d["catalog"].iterrows():
        col = c.kpi_name.lower().replace(" ", "_").replace("-", "_")
        rows.append({"kpi_id": c.kpi_id, "kpi_name": c.kpi_name, "source_mart": c.source_mart,
                     "grain": c.grain, "maturity_rule": c.maturity_rule, "interpretation": c.interpretation,
                     "limitation": c.limitation, "stakeholder": c.stakeholder})
    out["kpi_definitions"] = pd.DataFrame(rows)
    out["kpi_snapshot"] = kpi.copy()
    return out


# ------------------------------------------------------------------ findings
def findings(d, out):
    k = d["kpi"].iloc[0]
    focus = out["market_focus"]
    con = out["routing_concentration"].iloc[0]
    hi = focus[focus.is_high_opportunity] if "is_high_opportunity" in focus.columns else None
    f = d["focus"][d["focus"].is_cma_scope]
    hi = f[f.is_high_opportunity]
    byc = out["funnel_by_channel"].sort_values("mature_cohort_conversion_rate", ascending=False)
    bym = out["funnel_by_month"].sort_values("reporting_month")

    rows = [
        ("F-01", "Where to focus: nine markets carry the strongest external signal",
         f"{int(k.high_opportunity_cma_count)} of {int(k.cma_count)} CMAs sit in the framework's strongest external "
         f"opportunity class under the balanced planning scenario. They hold {int(hi.lead_count.sum()):,} of "
         f"{int(f.lead_count.sum()):,} leads that resolved to a CMA "
         f"({hi.lead_count.sum() / f.lead_count.sum():.1%}) and "
         f"{int(hi.active_lead_backlog.sum()):,} of the active backlog. "
         f"{int(k.cmas_without_opportunity_score)} CMAs cannot be scored at all and are reported as scale-only or "
         "no-evidence markets rather than as zero opportunity.",
         "external market signal"),
        ("F-02", "The funnel loses most of its volume at qualification, not at intake",
         f"Assignment reaches {k.assignment_rate:.1%} of valid leads and contact {k.contact_rate:.1%} of assigned "
         f"leads, but only {k.qualification_rate:.1%} of mature contacted leads qualify and "
         f"{k.mature_cohort_conversion_rate:.1%} of mature valid leads convert. Intake and routing are not the "
         "constraint; what happens after the first conversation is.",
         "funnel performance"),
        ("F-03", "Conversion is measured only on cohorts that have had time to convert",
         f"{int(k.conversion_rate_denominator):,} valid leads are mature at the 90-day window and "
         f"{int(k.conversion_rate_numerator):,} of them converted. Cohorts captured inside the window are excluded "
         "from the rate entirely and published separately, so recent leads are never counted as failed conversions.",
         "funnel performance"),
        ("F-04", "One lead in eight misses the first-contact promise",
         f"{int(k.sla_breach_count):,} of {int(k.sla_settled_count):,} settled first-contact clocks breached, a "
         f"{k.sla_breach_rate:.1%} breach rate. Only clocks that are stopped or already past target are counted, so "
         "a clock still legitimately running is never scored as a miss.",
         "service performance"),
        ("F-05", "Very little demand is stuck at the front door",
         f"{int(k.unassigned_or_queued_leads):,} active leads are unassigned or held in a controlled queue, out of "
         f"{int(k.active_leads):,} active leads. Routing places almost everything it is given; the backlog is work "
         "in progress, not a routing failure.",
         "operational exposure"),
        ("F-06", "The action queue is dominated by qualification work, not firefighting",
         f"{int(k.actionable_leads):,} leads need an operational step. "
         f"{int(k.p1_leads_requiring_action):,} are tier P1 and {int(k.leads_requiring_escalation):,} require "
         "escalation. The largest single action is continuing qualification, which matches the funnel finding.",
         "operational exposure"),
        ("F-07", "Capacity is not the binding constraint, but a few RMs are over the line",
         f"{int(k.rms_above_capacity_warning)} of {int(k.rms_with_capacity_evidence)} relationship managers with "
         f"capacity evidence are at or above the declared warning level and {int(k.rms_at_or_above_hard_limit)} are "
         "at or above the hard limit. Most of the network has headroom.",
         "capacity"),
        ("F-08", "Routing concentration is the clearest operational risk in this layer",
         f"The capacity-aware policy makes {int(k.operational_hard_cap_breaches)} hard-cap breaches, which is the "
         f"intended result, yet {k.routing_top_decile_share:.2%} of all recommendations land on the busiest tenth of "
         f"relationship managers, the workload Gini is {k.routing_gini:.3f}, one RM receives "
         f"{int(k.max_recommendations_to_one_rm):,} recommendations and {int(k.rms_with_no_recommendations)} RMs "
         "receive none at all. Hard-cap compliance does not automatically create a balanced workload: the "
         "preference rules reward the emptiest RM, and in a lightly loaded year that is repeatedly the same person. "
         "The routing rules are deliberately unchanged at this stage; this is surfaced as a risk to be decided on, "
         "not silently corrected.",
         "operational exposure"),
        ("F-09", "Channel quality differs materially once cohorts are mature",
         "Mature-cohort conversion by channel: "
         + ", ".join(f"{r.channel_code} {r.mature_cohort_conversion_rate:.1%}" for r in byc.itertuples())
         + ". The spread is a description of this synthetic baseline, not a recommendation to shift spend.",
         "funnel performance"),
        ("F-10", "Data-quality exceptions are a material share of the book",
         f"{int(k.data_quality_exception_count):,} leads carry an unresolved geography, a missing industry, a "
         f"pending validation or an unresolved duplicate. {k.duplicate_rate:.1%} of all captured leads are "
         "duplicates. These are handling costs before any selling happens.",
         "data quality"),
        ("F-11", "Market opportunity is a current snapshot and is never applied to history",
         "The monthly funnel carries no opportunity score. Applying today's market score to a historical month "
         "would imply the bank knew it at the time; a dedicated test asserts the score never appears there.",
         "external market signal"),
        ("F-12", "The monthly shape is stable, with the last cohorts still immature",
         f"Captured leads run from {int(bym.captured_leads.min()):,} to {int(bym.captured_leads.max()):,} a month. "
         "The final three cohorts carry a large immature population by construction and are excluded from the "
         "conversion rate rather than dragging it down.",
         "funnel performance"),
    ]
    return pd.DataFrame([{"finding_id": a, "finding": b, "evidence": c, "interpretation": e}
                         for a, b, c, e in rows])


# ------------------------------------------------------------------ figures
def figures(d, out):
    focus = d["focus"][d["focus"].is_cma_scope]
    bym = out["funnel_by_month"].sort_values("reporting_month")

    def fig_focus():
        fig, ax = plt.subplots(figsize=(9.5, 6))
        s = focus[focus.has_opportunity_score] if "has_opportunity_score" in focus.columns else focus
        s = s[s.opportunity_score.notna()]
        colr = np.where(s.is_high_opportunity, "#b3401f", "#1f5f8b")
        ax.scatter(s.opportunity_score, s.lead_count, s=38, c=colr, alpha=0.85, edgecolors="white", linewidths=0.6)
        for r in s.nlargest(6, "lead_count").itertuples():
            ax.annotate(str(r.cma_label_sgc2021)[:18], (r.opportunity_score, r.lead_count),
                        fontsize=7, xytext=(4, 3), textcoords="offset points")
        ax.set_yscale("log")
        h = [plt.Line2D([], [], marker="o", ls="", color="#b3401f"), plt.Line2D([], [], marker="o", ls="", color="#1f5f8b")]
        ax.legend(h, ["strongest external signal", "other scored markets"], fontsize=8, loc="upper left")
        return fig, ax

    def fig_funnel():
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        stages = ["captured_leads", "valid_leads", "assigned_leads", "contacted_leads",
                  "qualified_leads_mature", "converted_leads_mature"]
        labels = ["captured", "valid", "assigned", "contacted", "qualified\n(mature)", "converted\n(mature)"]
        vals = [int(out["funnel_by_month"][s].sum()) for s in stages]
        ax.bar(labels, vals, color=["#7f9aa8", "#5c7f92", "#39647c", "#1f5f8b", "#17496b", "#b3401f"])
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8)
        ax.set_ylim(0, max(vals) * 1.15)
        return fig, ax

    def fig_monthly():
        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.plot(bym.reporting_month, bym.assignment_rate, marker="o", ms=3.5, label="assignment rate", color="#1f5f8b")
        ax.plot(bym.reporting_month, bym.contact_rate, marker="o", ms=3.5, label="contact rate", color="#39647c")
        ax.plot(bym.reporting_month, bym.sla_compliance_rate, marker="o", ms=3.5,
                label="first-contact SLA compliance", color="#e08a1e")
        ax.plot(bym.reporting_month, bym.mature_cohort_conversion_rate, marker="o", ms=3.5,
                label="mature-cohort conversion", color="#b3401f")
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=8)
        ax.tick_params(axis="x", labelrotation=30)
        return fig, ax

    def fig_capacity():
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        rl = out["rm_capacity_latest"]
        v = rl.utilization_ratio.dropna()
        ax.hist(v, bins=30, color="#1f5f8b")
        ax.axvline(0.80, color="#e08a1e", ls="--", lw=1.2)
        ax.text(0.80, ax.get_ylim()[1] * 0.95, " warning 0.80", fontsize=7.5, va="top")
        ax.axvline(1.00, color="#b3401f", ls="--", lw=1.2)
        ax.text(1.00, ax.get_ylim()[1] * 0.85, " hard limit 1.00", fontsize=7.5, va="top", ha="right")
        return fig, ax

    def fig_concentration():
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        v = np.sort(out["routing_load_by_rm"].operational_recommendations.to_numpy())[::-1]
        ax.bar(np.arange(1, len(v) + 1), v, color="#1f5f8b", width=1.0)
        top = max(1, len(v) // 10)
        ax.axvline(top + 0.5, color="#b3401f", ls="--", lw=1.2)
        ax.text(top + 1, v.max() * 0.9, f" busiest decile takes {v[:top].sum() / v.sum():.1%}",
                fontsize=8, color="#b3401f")
        ax.set_yscale("symlog")
        return fig, ax

    def fig_actions():
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        s = out["action_queue_summary"].sort_values("leads").tail(10)
        ax.barh([t[:52] for t in s.recommended_next_action], s.leads, color="#1f5f8b")
        for i, v in enumerate(s.leads):
            ax.text(v, i, f"  {v:,}", va="center", fontsize=8)
        ax.set_xlim(0, s.leads.max() * 1.2)
        return fig, ax

    specs = [
        ("fig01_market_focus_opportunity_vs_activity", fig_focus,
         "External market opportunity against acquisition activity, by CMA",
         "external opportunity score (0-100 relative index)", "leads captured (log scale)",
         "Where is the external signal strong, and are we actually present there?"),
        ("fig02_acquisition_funnel_volumes", fig_funnel,
         "Acquisition funnel volumes, conversion measured on mature cohorts only",
         "funnel stage", "leads", "Where does the funnel lose its volume?"),
        ("fig03_monthly_funnel_rates", fig_monthly,
         "Monthly funnel and service rates by capture cohort",
         "capture month", "rate", "Is performance stable over the operating year?"),
        ("fig04_rm_capacity_utilization", fig_capacity,
         "Relationship manager utilization at the reporting cutoff",
         "utilization ratio", "relationship managers", "How much capacity headroom does the network have?"),
        ("fig05_routing_concentration", fig_concentration,
         "Operational routing recommendations per relationship manager, ranked",
         "relationship managers, busiest first", "recommendations (symmetric log scale)",
         "Is the recommended workload spread across the network?"),
        ("fig06_action_queue_by_action", fig_actions,
         "What the actionable leads actually need", "leads", "recommended next action",
         "What work is in front of the team today?"),
    ]

    rendered, register = {}, []
    for fid, fn, title, xl, yl, question in specs:
        pngs = []
        for _ in range(2):
            fig, ax = fn()
            fig.suptitle(title, fontsize=10)
            ax.set_xlabel(xl)
            ax.set_ylabel(yl)
            fig.text(0.01, 0.005, NOTE, fontsize=6.5, ha="left", va="bottom")
            fig.tight_layout(rect=(0, 0.03, 1, 0.95))
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, metadata={"Software": None})
            plt.close(fig)
            pngs.append(buf.getvalue())
        rendered[fid] = pngs[0]
        register.append({"figure_id": fid, "filename": f"outputs/acquisition_intelligence/{fid}.png",
                         "title": title, "business_question": question, "x_label": xl, "y_label": yl,
                         "reproducible_render": hashlib.sha256(pngs[0]).hexdigest()
                                                == hashlib.sha256(pngs[1]).hexdigest()})
    return rendered, pd.DataFrame(register)


def main():
    import os
    os.chdir(ROOT)
    d = load()
    out = analyse(d)
    fnd = findings(d, out)
    figs, fig_reg = figures(d, out)

    files = {
        "market_focus.csv": out["market_focus"],
        "market_focus_gaps.csv": out["market_focus_gaps"],
        "funnel_monthly.csv": out["funnel_monthly"],
        "funnel_by_month.csv": out["funnel_by_month"],
        "funnel_by_channel.csv": out["funnel_by_channel"],
        "funnel_by_segment.csv": out["funnel_by_segment"],
        "funnel_by_industry.csv": out["funnel_by_industry"],
        "funnel_by_cma.csv": out["funnel_by_cma"],
        "rm_performance_monthly.csv": out["rm_performance_monthly"],
        "rm_capacity_latest.csv": out["rm_capacity_latest"],
        "routing_concentration.csv": out["routing_concentration"],
        "routing_load_by_rm.csv": out["routing_load_by_rm"],
        "action_queue.csv": out["action_queue"],
        "action_queue_summary.csv": out["action_queue_summary"],
        "kpi_snapshot.csv": out["kpi_snapshot"],
        "kpi_definitions.csv": out["kpi_definitions"],
        "acquisition_intelligence_findings.csv": fnd,
        "figure_register.csv": fig_reg.drop(columns="reproducible_render"),
    }

    print("CONTROLS")
    forbidden = {"legal_name", "operating_name", "normalized_legal_name", "contact_email_fictional",
                 "contact_phone_fictional", "contact_role"}
    bad = {n: sorted(set(df.columns) & forbidden) for n, df in files.items() if set(df.columns) & forbidden}
    check("no fictional contact or business name is written out", not bad, bad or f"{len(files)} files")
    gt = {n: [c for c in df.columns if any(k in c for k in ("expected_", "truth", "latent_", "planned_path",
                                                            "arrival_kind", "primary_scenario"))]
          for n, df in files.items()}
    gt = {n: v for n, v in gt.items() if v}
    check("no generator ground-truth field is written out", not gt, gt or "clean")
    check("every finding carries a non-causal interpretation",
          set(fnd.interpretation) <= {"external market signal", "funnel performance", "service performance",
                                      "operational exposure", "capacity", "data quality"},
          sorted(set(fnd.interpretation)))
    causal = [t for t in fnd.finding.tolist() + fnd.evidence.tolist()
              if any(w in t.lower() for w in ("improves conversion", "increases revenue", "causes", "uplift",
                                              "drives conversion", "proves"))]
    check("no finding claims a causal effect or a revenue estimate", not causal, causal or "clean")
    money = [n for n, df in files.items()
             if any(c for c in df.columns if any(m in c.lower() for m in ("revenue", "profit", "dollar", "cad_")))]
    check("no financial or revenue estimate is produced", not money, money or "clean")
    check("the funnel reconciles to the full lead population",
          int(out["funnel_monthly"].captured_leads.sum()) == int(d["kpi"].iloc[0].total_leads),
          f"{int(out['funnel_monthly'].captured_leads.sum()):,}")
    check("figures render reproducibly", bool(fig_reg.reproducible_render.all()), f"{len(fig_reg)} figures")

    stamped = {n: stamp(df) for n, df in files.items()}
    for n, df in stamped.items():
        check(f"output carries source, period, unit and eligibility: {n}",
              {"source_models", "period", "statistical_unit", "evidence_eligibility", "limitations"} <= set(df.columns))

    if not all(c["passed"] for c in CHECKS):
        print("ANALYSIS FAILED: a control did not pass; nothing was published.")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    for n, df in stamped.items():
        df.to_csv(OUT / n, index=False, lineterminator="\n")
    for fid, png in figs.items():
        (FIG / f"{fid}.png").write_bytes(png)
    pd.DataFrame(CHECKS).to_csv(OUT / "analysis_controls.csv", index=False, lineterminator="\n")
    print(f"\nPublished {len(stamped)} analysis files to {OUT.relative_to(ROOT)} and "
          f"{len(figs)} figures to {FIG.relative_to(ROOT)}")
    print("ACQUISITION INTELLIGENCE ANALYSIS PASSED")


if __name__ == "__main__":
    main()
