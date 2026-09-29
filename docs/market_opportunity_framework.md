# Market Opportunity Framework

**Canadian SME Acquisition Strategy.** This document describes a transparent framework for comparing Canadian census metropolitan areas (CMAs) on **external evidence** of SME acquisition opportunity.

**The business question:**

> Which Canadian metropolitan markets show the strongest external evidence of SME acquisition opportunity, and how stable is that assessment under reasonable alternative business priorities?

**Framing:**
- **Primary stakeholder:** Director, Business Banking Sales Strategy.
- **Decisions supported:**
  - which markets deserve deeper acquisition planning;
  - which show scale, momentum or commercial-fit potential;
  - where signals are stable or uncertain;
  - which markets need separate treatment because of limited evidence;
  - how conclusions change when priorities change.
- **What the score is:** an *external market signal* built from public aggregates. It is not a prospect or lead list, and not expected revenue, lead volume, conversion probability or profitability.
- **Not yet considered:** existing bank market share, current customers, branch coverage, relationship-manager capacity, credit risk, acquisition cost, internal conversion, internal revenue and customer profitability. External opportunity alone does not decide investment.
- **Internal data later in the project** will be synthetic. Hypothetical internal issues are illustrations, not facts about any real bank.

Labels: **FACT: DATA** (reproduced from outputs), **INFERENCE**, **DECISION**.

---

## 1. Populations

- **Full framework: **35** CMAs** (Tiers 1 and 2) with complete table-0722 momentum history. These are the only markets that receive a full score, scenario ranks, sensitivity statistics and a classification.
  - The framework mart has **140** rows: 35 CMAs × 4 core scenarios.
- **Scale-only: **6** Tier 3 CMAs**: Fredericton, Drummondville, Red Deer, Kamloops, Chilliwack and Nanaimo. They were census agglomerations in 2016, so they have no momentum series.
  - They receive SME-location scale and composition context, with **41-CMA** percentiles. These aren't comparable with the 35-CMA framework percentiles.
  - They get an explicit `full_score_eligible = false`.
  - Nothing is imputed. They have no score, rank or momentum classification (`mart_cma_scale_only_context`).

## 2. Indicator decisions

**DECISION** (`indicator_decision_register.csv`). The six indicators retained by the market exploration were tested. Every supporting, rejected and unresolved indicator keeps its exploration classification and never enters a score.

| Indicator | Business name | Source mart | Final role |
|---|---|---|---|
| C01 | Addressable SME employer-location scale | `fact_cma_market_scale_snapshot` | core score (scale pillar) |
| C03 | Business dynamism: opening rate, a measure of churn intensity, not net growth | `fact_cma_momentum_monthly` | core score (dynamism pillar) |
| C05 | Recent net-formation rate (12 months) | `fact_cma_momentum_monthly` | core score (momentum pillar, half) |
| C09 | 36-month net-formation trend | `fact_cma_momentum_monthly` | core score (momentum pillar, half) |
| C15 | Commercial fit: 20–99 employee share of SME locations | `fact_cma_size_composition_snapshot` | core score (commercial-fit pillar) |
| C10 | Volatility of net formation | `fact_cma_momentum_monthly` | sensitivity scenario only; confidence measure |

**Why C10 is not in the core score:**
- Raw volatility falls with market size (Spearman -0.65), so a raw penalty would punish small markets a second time.
- C10 therefore enters only the *stability-aware* scenario, and in scale-adjusted form: the residual of log volatility regressed on log scale, which is unrelated to size (-0.04).
- C10 also underpins the **momentum signal-clarity** confidence measure. It asks whether the 12-month mean net-formation rate exceeds two standard errors (SD/√12). This is a heuristic that ignores autocorrelation.

Every indicator's lineage runs to an existing dbt mart. A dbt test recomputes each raw value from those marts, and the values agree with the validated market-exploration outputs to within 1e-10 (`indicator_lineage_check.csv`).

## 3. Transformations and normalization

- **Transformations (DECISION):**
  - log10 for scale: skewness falls from 3.49 to 1.10. The three largest CMAs hold 52.1% of 41-CMA locations.
  - identity for rates and shares;
  - log10 of the SD for volatility;
  - a scale-adjusted residual for the stability component.
- **Raw values are always preserved.** Outliers are kept, never removed.
- **Primary normalization** (declared before any score was computed): **percentile rank within the 35 CMAs, with average ranks for ties.** The formula is 100 × (average rank − 1) / (n − 1).
  - It is bounded 0–100 by construction.
  - Extreme markets can't dominate it, so no trimming is needed.
  - It is deterministic, and it puts every component on the same scale.
  - Components are oriented first, so a **higher normalized value always means stronger evidence**. A lower-is-better component is negated before ranking.
- **Alternatives, computed in dbt for sensitivity only:**
  - min-max;
  - min-max after capping at the Tukey fences (raw value preserved, caps recorded, results compared with and without the cap);
  - a clipped robust z-score (median, IQR/1.349, ±3).

  Balanced ranks under these alternatives correlate 0.87-0.88 with the primary method (`normalization_comparison.csv`).
- **What percentile ranks cost:** they discard magnitude. Most CMAs' net-formation differences are within month-to-month noise, yet percentiles spread them across 0–100. The momentum signal-clarity measure makes that visible, see §7.

## 4. Pillars, scenarios and the score

**Pillars.** Each pillar score is the weighted mean of its normalized components. The momentum pillar averages C05 and C09 (0.5 each), so having two correlated components (Spearman 0.75) gives it no extra weight.

| Pillar | Components |
|---|---|
| Scale | C01 |
| Momentum | C05, C09 |
| Dynamism | C03 |
| Commercial fit | C15 |
| Stability | C10, scale-adjusted (stability-aware scenario only) |

**Scenarios** (`seeds/opportunity/opportunity_weight_scenarios.csv`). Weights are set at pillar level, are nonnegative and sum to 1. They were declared before scoring, and their hashes are recorded in `configuration_declaration.json`.

| Scenario | Scale | Momentum | Dynamism | Commercial fit | Stability | Business interpretation |
|---|---|---|---|---|---|---|
| Balanced | 0.25 | 0.25 | 0.25 | 0.25 | 0 | Equal emphasis; stability shown only as confidence context |
| Scale-led | 0.50 | 0.20 | 0.15 | 0.15 | 0 | Coverage and volume first |
| Growth-led | 0.20 | 0.50 | 0.15 | 0.15 | 0 | Expanding employer-business populations first |
| Stability-aware | 0.225 | 0.225 | 0.225 | 0.225 | 0.10 | Balanced, tempering erratic markets without letting stability dominate |
| Unadjusted volatility penalty *(comparison only)* | 0.225 | 0.225 | 0.225 | 0.225 | 0.10 (raw) | Measures the small-market double penalty |

No scenario is treated as the correct one.

**The score:**
- Score = Σ (pillar weight × pillar score), on a 0–100 scale.
- It is a *relative* index within the 35 CMAs.
- Every published row carries the raw, transformed and normalized components, the pillar scores, the weights, each pillar's contribution, the score, the rank, the percentile and the metadata.
- Every score can be recalculated from the published components and weights. The validation recalculates all 140 scores from the CSVs.
- **Ranks:**
  - The rank is 1 for the strongest market in each scenario.
  - Scores are rounded to 1e-9 before ranking, so mathematically equal scores tie exactly and share a rank.
  - `score_position` breaks ties by CMA code, and `tied_markets` records the tie.

## 5. Classification rules

The rules are applied in order, per scenario, using thresholds from `opportunity_decision_thresholds.csv`. They describe an external signal, not an investment recommendation.

1. **Stronger external opportunity signal:** score percentile ≥ 75 (top quartile of the scenario).
2. **Scale-led external opportunity signal:** otherwise, scale pillar ≥ 75 and momentum pillar < 50.
3. **Momentum-led external opportunity signal:** otherwise, momentum pillar ≥ 75 and scale pillar < 50.
4. **Moderate external opportunity signal:** otherwise, score percentile ≥ 25.
5. **Weaker current external signal (monitor):** otherwise.

Scale-only markets carry **Scale-only limited evidence** in their own view.

## 6. Worked example: the top balanced-scenario market

All values below come from `cma_opportunity_components.csv` and `cma_opportunity_scores.csv`.

**Market: **Halifax**, balanced scenario.**

1. **Raw values:**
   - 15,668 SME employer locations (December 2025);
   - opening rate 0.04538 per month;
   - net-formation rate 0.002745 per month;
   - 36-month trend 0.000730 per year;
   - 20–99 share 0.1431.
2. **Transformation:** scale log10 = 4.1950. The other components are unchanged.
3. **Normalized (percentile within 35 CMAs):** C01 normalized 67.6; C03 normalized 55.9; C05 normalized 100.0; C09 normalized 91.2; C15 normalized 82.4.
4. **Pillars:** scale 67.6, momentum 95.6, dynamism 55.9, commercial fit 82.4. Momentum is the mean of 100.0 and 91.2.
5. **Weights (balanced):** 0.25 each on scale, momentum, dynamism and commercial fit; 0 on stability.
6. **Score:** 0.25 × (67.6 + 95.6 + 55.9 + 82.4), giving a balanced score **75.37** and rank 1.
7. **Classification:** *Stronger external opportunity signal*. Its score percentile is in the top quartile, and Halifax stays in the top quartile under all four scenarios.

## 7. Sensitivity and stability findings

**FACT: DATA** (`cma_opportunity_sensitivity.csv`, `scenario_comparison.csv`, `sensitivity_validation.csv`).

- **Stable stronger signal.** **6** CMAs are in the top quartile under all four scenarios: Halifax, Winnipeg, Saskatoon, Edmonton, Calgary and Quebec.
  - In **5,000** weight draws (pillar weights 0.10–0.40, renormalized, seed 20260927), Halifax, Winnipeg, Saskatoon, Edmonton, Calgary and Windsor are in the top quartile in at least 80% of draws.
  - This measures robustness to weighting assumptions, not statistical confidence.
- **Assumption-sensitive.** **14** CMAs are assumption-sensitive: their rank range across scenarios is 9 or more, or they reach the top quartile in some scenarios but not all. They are Windsor, Kitchener - Cambridge - Waterloo, London, Vancouver, Moncton, Ottawa - Gatineau, Lethbridge, Thunder Bay, Toronto, Saint John, Hamilton, Saguenay, Oshawa and Montréal.
  - The largest movement is Toronto's, 20 ranks between scale-led and growth-led.
- **Stable middle and weaker.** **10** CMAs form a stable middle, and **5** are stably weaker: Barrie, Greater Sudbury, Brantford, Belleville - Quinte West and Peterborough.
- **Drivers:**
  - Scale-driven, where the scale pillar exceeds momentum by 25 or more: Calgary, Kitchener - Cambridge - Waterloo, Vancouver, Ottawa - Gatineau, Toronto, Hamilton, Oshawa and Montréal.
  - Momentum-driven: Halifax, Saskatoon, Moncton, Abbotsford - Mission, Lethbridge, Thunder Bay, Saint John, Kingston and Saguenay.
  - The rest are neither scale- nor momentum-driven.
- **Balanced across all four core pillars.** Using a descriptive rule (every core pillar at 50 or above; not a classification), the balanced markets are Halifax, Winnipeg, Saskatoon and Windsor.
- **Leave-one-indicator-out.** Removing scale, commercial fit or dynamism moves more markets than removing either momentum component. That's expected: each of those three is a whole pillar, while C05 and C09 share one.
- **Normalization.** Only 3-4 markets shift by 9 or more ranks under an alternative method.
- **Momentum reliability.** Only **3** of 35 CMAs have a 12-month net-formation direction clearly distinguishable from month-to-month noise.
  - Net formation persists only weakly between consecutive 12-month windows (Spearman 0.36).
  - Every momentum-led classification rests on a 12-month signal that is unclear under this test.
  - **INFERENCE:** momentum-led placements are provisional and should be read together with the 36-month trend.

## 8. Double-counting and fairness checks

**FACT: DATA** (`double_counting_checks.csv`). The thresholds were declared before scoring. Results that exceed them are reported, not tuned away.

| Check | Result |
|---|---|
| **Size dominance** | Score and log scale correlate at **0.58** (balanced), 0.30 (growth-led) and 0.55 (stability-aware), below the 0.8 threshold. In the scale-led scenario the correlation is **0.91** (scale-led), and C01 explains **81%** of the scale-led score variance, so **the scale-led scenario is essentially a size ordering**; read it as a size view. No single component reaches 50% of the variance in the other scenarios. |
| **Volatility double penalty** | An unadjusted penalty moves the smallest-quartile markets down about 1.8 ranks on average, while others gain about 0.6. The scale-adjusted version doesn't. |
| **C05 and C09 duplication** | Spearman 0.75. They are averaged within one pillar, so each has an effective weight of 0.125 in balanced. |
| **Hidden influence** | Momentum explains 38% of balanced score variance against a 25% weight (`component_contribution_analysis.csv`). It is nearly independent of the other pillars, while dynamism and commercial fit partly offset each other (Spearman -0.74). Dynamism also carries some scale influence (Spearman 0.49 with log scale). |
| **Commercial fit** | It favours mid-sized markets, especially in Quebec, Northern Ontario and Atlantic Canada, and runs against scale and dynamism. Removing it moves balanced ranks by 4 places at the median and up to 12 (Quebec). It materially shapes conclusions, and its definition should be reviewed with sales strategy. |

## 9. Supporting context and ISED

- **Context columns sit beside the score and never enter it.** They are prefixed `context_`, and scores recalculate without them. The context covers:
  - the ISED 2023 external-financing request rate, with its quality code, for Tier 1 CMA rows only;
  - the evidence tier;
  - the boundary caveat;
  - momentum E-flags.
- **ISED limits:**
  - the estimates are from 2023;
  - they cover SMEs with at least $30,000 in revenue;
  - they carry sampling error, and most CMA differences are not distinguishable;
  - regional values are never attached to CMAs.

## 10. Tier 3 markets

The six scale-only CMAs sit between the 2.5th and 22.5th percentile of 41-CMA scale.
- The highest 20-99 shares are Drummondville (87.5th percentile) and Kamloops (82.5th percentile).
- Nanaimo and Kamloops are the largest.

**DECISION:** review them qualitatively, for example with local market knowledge, rather than by score.

## 11. Outputs and how to read them

| Output | Content |
|---|---|
| `data/analysis/market_opportunity/cma_opportunity_scores.csv` | One row per CMA and scenario: score, rank, position, percentile, pillars, weights, contributions, classification, confidence and metadata |
| `cma_opportunity_components.csv` | Raw → transformed → oriented → normalized value for every component (all four normalizations), with caps, ties, lineage and period |
| `cma_opportunity_sensitivity.csv` | Rank ranges, top-quartile counts and perturbation frequency, leave-one-out shifts, normalization and volatility shifts, stability group and driver |
| `cma_opportunity_classifications.csv` | Classification per scenario, with consistency, stability group and signal clarity |
| `cma_scale_only_context.csv` | The six Tier 3 CMAs: scale, 41-CMA percentiles, composition, limitation |
| `scenario_comparison.csv` | Per scenario: score range, correlation with balanced, size correlation, largest component |
| `market_opportunity_findings.csv` | **15** findings, each with evidence, limitation and label |
| `component_contribution_analysis.csv` | Share of score variance from each component, per scenario, against its effective weight |
| `data/validation/opportunity_framework/` | Decision register, indicator lineage check, transformation and normalization comparisons, weight and score recalculation checks, population checks, double counting, sensitivity, framework checks, configuration declaration and summary |
| `outputs/market_opportunity_framework/` | Six figures: scale versus momentum matrix; component heatmap; scenario ranks; contributions; top-quartile frequency; scored versus scale-only markets. Their register is `figure_register.csv` |

## 12. Reproducing the framework

```bash
powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1
```

```bash
powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope Opportunity
```

**The opportunity section runs, in order:**
1. confirm the base marts match their reconciled checksums and the configuration is unchanged;
2. `dbt seed`, `dbt run` and `dbt test` on `tag:opportunity`;
3. the analysis, `src/analysis/13_analyze_market_opportunity_framework.py`;
4. the independent validation, `src/validation/14_validate_market_opportunity_framework.py`.

**What the validation checks:**
- it recalculates every published score, rank and classification;
- it runs the analysis twice and requires byte-identical outputs;
- it checks the numbers in this document.

**The dbt implementation:**
- intermediate models `int_opportunity__indicator_inputs`, `__transformed_indicators`, `__normalized_indicators`, `__pillar_scores`, `__scenario_scores` and `__leave_one_out_scores`;
- marts `mart_cma_opportunity_framework`, `mart_cma_opportunity_sensitivity` and `mart_cma_scale_only_context`.

## 13. What the framework does not conclude, and what comes next

**It does not conclude:**
- which markets the bank should enter;
- expected revenue, lead volume or conversion;
- that one weighting is correct.

**What an investment decision still needs:**
- current market share and customers;
- branch coverage;
- relationship-manager capacity;
- credit risk appetite;
- acquisition cost;
- conversion history;
- customer profitability.

The next step is to design the synthetic internal CRM layer and combine it with this external view, keeping the two separate.
