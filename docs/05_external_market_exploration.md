# External Market Exploration (Phase H)

**Canadian SME Acquisition Strategy.** This document describes what the validated Phase G analytical views say about the 41 CMAs. It also profiles candidate indicators that a later opportunity framework could test.

It is **descriptive only**. It does not:
- calculate a composite score or assign weights;
- rank markets or recommend priority markets;
- create CRM data, define routing rules or build a dashboard.

**Where the outputs are:**
- analytical tables: `data/analysis/market_exploration/`;
- figures: `outputs/phase_h_market_exploration/`;
- script: `src/analysis/10_explore_market_opportunity.py`.

The script reads only the Phase G views, which are checked against their manifest hashes, plus the validated phase summaries. It checks that every number quoted here matches its outputs.

Labels: **FACT: DATA** (reproduced by the script), **INFERENCE**, **DECISION**.

---

## 1. Questions, units and periods

| Question | Source view | Unit | Period |
|---|---|---|---|
| Market scale | table 1097 (scale, sector) | employer **location** (not a business, client or prospect) | December 2025 (REF_DATE 2025-07) |
| Recent momentum | table 0722 (T004, all sizes) | enterprise counts; monthly rate = count ÷ active enterprises at t−1 | recent 2025-06 to 2026-05; prior 2024-06 to 2025-05; 36 months 2023-06 to 2026-05 |
| Small-business momentum | table 0722, 1–4 and 5–19 bands (kept separate) | as above | recent 12 months |
| Industry and size composition | table 1097 | location shares | December 2025 |
| Financing context | ISED SFGSME 2023 | survey estimate (% of SMEs, dollars, weighted counts) | calendar 2023 |

**Momentum labelling.** All momentum measures are labelled *all-employer-business momentum context*, never SME-specific. Rates use 0722's own active enterprises as the denominator.

**Window boundary.** The first rate in the 36-month window (2023-06) uses active enterprises from 2023-05, which is outside the window.

**Pandemic months.** No analysis window touches the pandemic period (2020-03 to 2021-12). Those months stay flagged in the full monthly series (`cma_momentum_monthly_series.parquet`), which is kept for all 137 months.

## 2. Market scale (table 1097)

- **FACT: DATA.** The 41 CMAs contain **1,009,239** SME employer locations (1–499 employees). The median CMA has **7,986**; the largest, Toronto, has 264,670.
- **FACT: DATA. Scale is concentrated.** The 3 largest CMAs hold **52.1%** of the 41-CMA total and the 5 largest **64.1%**. **3** CMAs account for half of all SME locations. **INFERENCE:** a raw-count scale indicator would be dominated by a few markets, so Phase I needs a log or robust transform.
- **FACT: DATA.** The 1–499 band is 99.5%–99.9% of employer locations in every CMA. Its share therefore doesn't distinguish markets, and 500+ is reported separately.
- **FACT: DATA.** Unclassified locations are **6.7%** of SME locations at the median CMA; the highest share is in St. John's. They are never allocated to sectors.
- **FACT: DATA.** Public administration (NAICS 91) is **0.35%** of SME locations at the median CMA, and at most 2.1% (Fredericton). It is kept out of the 19-sector denominator.
- **FACT: DATA.** Median SME locations are 15,429 in Tier 1, 5,603 in Tier 2 and 4,060 in Tier 3; Tier 1 holds **90.4%** of the total. **INFERENCE:** tier and scale are confounded, because Tier 1 holds the large CMAs.

## 3. Recent momentum (table 0722, 35 CMAs)

- **FACT: DATA.** Over 2025-06 to 2026-05, the median opening rate **4.48%** per month and the closure rate **4.38%** almost cancel out.
- **FACT: DATA.** **27** of 35 CMAs had a positive mean net-formation rate. The median net-formation rate is only 0.033% per month.
- **Recent versus prior 12 months.** The net-formation rate rose in **22** CMAs, but **0** exceed the 2-SE noise heuristic.
  - The heuristic is built from monthly standard deviations and ignores autocorrelation, so it is indicative only. **INFERENCE:** no CMA shows a clear acceleration or deceleration.
- **36-month linear trend.** The trend in net-formation rate is positive in **16** CMAs.
- **Net formation versus the change in active enterprises (INFERENCE).** Summed monthly net formation (openings − closures) differs from the change in active enterprises between 2025-05 and 2026-05. Reopenings, exits and separate seasonal adjustment all widen the gap. Both are reported, and neither is used to validate the other.
- **Volatility.** Month-to-month volatility falls as market size rises (Spearman ρ −0.64): smaller CMAs have noisier rates.
- **Coverage (FACT: DATA).** Every eligible CMA has 12 of 12 usable months for every core series in the recent window, with **0** E-flagged and 0 unavailable observations.
- **Tier 3.** Tier 3 CMAs have no 0722 history and appear in no momentum table or axis.

## 4. Small-business momentum (optional bands)

- **FACT: DATA.** Complete 12-month opening and closing rates exist for the 1–4 band in **35** CMAs, but for the 5–19 band in **22** only. Active counts for 5–19 are complete in all 35.
- **DECISION:** the bands stay separate. They are not an SME total, and they say nothing about firms with 20–499 employees.
- **Phase I use:**
  - The 1–4 opening rate is almost identical to the all-size opening rate (ρ 0.96), so it adds little.
  - The 5–19 rate is missing in 13 CMAs, so it would bias a 35-CMA comparison.

## 5. Industry composition (table 1097)

- **Method.** Sector shares use classified SME locations in the 19 business sectors as their denominator, and they reconcile to 1 in every CMA.
- **FACT: DATA. Largest sector.** Health care and social assistance is the largest SME sector in 17 of 41 CMAs.
- **FACT: DATA. Concentration.** Sector HHI ranges only from **0.085** to **0.105**, 9.6–11.8 effective sectors. Brantford is the most diversified and Ottawa–Gatineau the most concentrated. **INFERENCE:** diversification discriminates little between markets.
- **Specialization.** The location quotient (LQ) is a CMA's sector share divided by that sector's share across all 41 CMAs; only 1097 values are used.
  - **FACT: DATA.** **47** CMA × sector cells are materially over-represented, and 81 under-represented.
  - "Materially" means an LQ of at least 1.5 (or at most 1/1.5), at least 10 locations, and a Poisson deviation (|z|) of 3 or more.
  - The largest is mining and oil and gas in Red Deer (LQ 8.8).
- **Small counts.** **39** of 779 CMA × business-sector cells have fewer than 10 SME locations, so their shares and LQs are unstable.

## 6. Size composition (table 1097, location-based)

- **FACT: DATA.** Locations with 1–4 employees are **51.8%** of SME locations at the median CMA (range 43.1%–64.3%). The 100–499 band is 1.95%. 500+ is reported only as a share of all employer locations.
- **Distinct profiles.** **3** CMAs have a band share at least 2 SD from the 41-CMA mean: Saguenay and Trois-Rivières in the 20–99 band, and Toronto in the 1–4 and 5–19 bands.
- **The 100–499 band.**
  - At CMA level its smallest count is **55** locations (Nanaimo), so it is usable there.
  - Phase F found that only 37.4% of CMA × sector × 100–499 cells have 10 or more locations. That cross isn't part of the analytical views.
- **Redundancy (INFERENCE).** The 1–4, 1–19 and 20–99 shares mirror each other (|ρ| 0.86–0.98), so only one of them carries independent information.

## 7. Financing context (ISED 2023)

- **Quality rules:**
  - A–D estimates are used descriptively.
  - E estimates carry a caution flag (**55** E in the profile).
  - F estimates are excluded from every comparison (**35** F).
  - `..` is never treated as zero.
- **Comparison rule.** Two estimates are called different only when their gap exceeds 2 × √(SE₁² + SE₂²). A CV is converted to an SE first.
  - Comparing a segment with the national figure is conservative, because the segment is part of the national population.
- **Results (FACT: DATA):**
  - External-financing request rate: 49.3% nationally. 4 of 24 CMAs are distinguishable from the national figure; **40 of 276** CMA pairs are distinguishable.
  - Debt request rate: 25.7% nationally. **26 of 276** pairs are distinguishable.
  - Debt approval rate: **53 of 276** pairs are distinguishable.
  - Product-level approval rates at CMA level include 20 F and 19 E estimates out of 96.
- **Rules kept:**
  - No percentage is converted into a count.
  - No one-way estimates are multiplied together.
  - No regional value is attached to a CMA. Tier 2 and Tier 3 have no ISED CMA fields.
- **DECISION:** ISED stays contextual.

## 8. Evidence coverage and quality

`cma_evidence_profile.csv` gives, for every CMA:
- tier and source periods;
- availability of momentum, scale, industry, size and ISED CMA context;
- E-flag and unavailable counts;
- sector rows with reconciled zeros;
- the 2016→2021 boundary caveat;
- eligibility for the primary analysis.

The six Tier 3 CMAs are marked *scale-only; not eligible for the primary momentum-based comparison*.

## 9. Candidate indicators

**DECISION** (`candidate_indicator_register.csv`, **28** candidates). Each candidate has a rationale, role, source, unit, formula, grain, period, eligible tiers and coverage. It also records a direction hypothesis, missing-value and quality treatment, distribution and outlier notes, redundancy risk and a status. No weights are assigned.

| Status | Candidates |
|---|---|
| Retain for Phase I testing | C01, C03, C05, C09, C10, C15 |
| Supporting context | C04, C06, C07, C11, C12, C16, C17, C18, C19, C20, C22, C23, C24, C26, C28 |
| Reject | C02, C14, C25, C27 |
| Unresolved | C08, C13, C21 |

**Retained:**
- **C01** SME employer locations (log scale);
- **C03** opening rate (a churn-intensity measure);
- **C05** net-formation rate;
- **C09** 36-month trend;
- **C10** volatility (depends on scale);
- **C15** the 20–99 share.

**Rejected:**
- **C02**, because it is near-constant;
- **C14**, because it duplicates C13 and C15;
- **C25**, because CMA-level product approvals are dominated by F and E estimates;
- **C27**, because sector momentum exists for only 8 CMAs.

**Unresolved:**
- **C08** acceleration: no change exceeds the noise heuristic;
- **C13** micro share: its direction depends on product strategy, and it mirrors C15;
- **C21** 5–19 opening rate: it is published in only 22 CMAs.

## 10. Redundancy and outliers

- **FACT: DATA.** `indicator_correlation_matrix.csv` covers **213** indicator pairs, each with its n and row basis:
  - 41 CMAs for 1097-only pairs;
  - 35 CMAs for pairs involving momentum (labelled *association between market attributes* when the units differ);
  - 24 CMAs for ISED pairs.

  Survey estimates are never paired with administrative counts.
- **Highly redundant pairs** (|ρ| ≥ 0.8):
  - opening vs closure rate (0.98);
  - net-formation rate vs active-enterprise growth (0.98);
  - opening rate vs the 1–4 opening rate (0.96);
  - the 1–19 vs 20–99 shares (−0.98).
- **Nearly unrelated:**
  - opening rate and net-formation rate (ρ −0.01): they measure different things;
  - scale and net-formation rate (0.08).
- **Level and change** are moderately related (0.59), which carries a regression-to-the-mean risk.
- **Outliers.** 13 of 24 quantitative candidates have Tukey extremes (the 1.5 × IQR rule).
  - The scale extremes (Toronto, Montréal, Vancouver and others) are genuine market size, not quality anomalies.
  - No extreme observation carries an E flag.
  - Nothing is removed or winsorized. Phase I should test robust scaling (median/IQR) or log transforms.

## 11. Findings, figures and limitations

**Findings.** `descriptive_findings.csv` holds **33** findings. Each has an evidence measure, source, unit, period, quality status, limitation, evidence label and an implication for Phase I.

**Figures** (static diagnostics, not a dashboard or a ranking; each has units, periods and a source note):
1. the distribution of SME-location scale;
2. opening rate against net-formation rate;
3. scale against net-formation rate, with Tier 3 in a separate strip and no rate;
4. a sector-specialization heatmap, with small-count cells marked;
5. size composition;
6. an evidence-coverage matrix;
7. ISED request rates with ±2 SE, E flagged and F excluded.

**Limitations:**
- 1097 counts locations, not firms.
- 0722 momentum is all-employer context and doesn't cover SMEs only.
- ISED has sampling error, a different unit and year (2023), and CMA estimates only for Tier 1.
- CMA boundaries differ between 2016 and 2021.
- The CMA × sector × size cross isn't in the views.

## 12. What Phase H does not conclude, and what Phase I must decide

**Phase H does not conclude** which markets are attractive, how indicators should be weighted, or which CMAs should be targeted.

**Phase H verdict (DECISION): the evidence is ready for Phase I to design a transparent opportunity framework.**
- **Decision set.** The 35-CMA decision set remains appropriate: it has complete, E-free recent momentum and full 1097 coverage.
- **Tier 3.** It should remain a separate scale-only view.

**Phase I must decide:**
- how to transform scale (log or robust);
- which one of each redundant pair to keep;
- the direction for HHI, size shares and closure rate;
- whether acceleration (C08) is worth using, given the noise;
- how ISED context is shown without scoring it;
- how Tier 3 is presented.

---

## Input source update

The introduction above describes the original inputs, the Parquet views in `data/processed/analytical_views/`. The script now reads the same 11 relations from the dbt marts (schema `analytics` in `data/warehouse/sme_banking.duckdb`). They reconcile exactly with those Parquet views, and every output, finding and figure in this document was reproduced byte for byte. The evidence is in `data/validation/dbt_migration/`; see [dbt_transformation_layer.md](dbt_transformation_layer.md).
