# MVP Analytical Scope (Phase F)

**Canadian SME Acquisition Strategy.** This document records which markets, measures and grains the first version of the analysis will use, and why. It is a design decision only: no analytical datasets have been built, no markets ranked and no scores calculated.

The evidence behind every decision is in `data/validation/mvp_grain/`, produced by `src/validation/08_select_mvp_grain.py`. That script also checks that the numbers quoted here match its outputs. The source validation it builds on is in [02_data_sources_and_validation.md](02_data_sources_and_validation.md).

Labels: **FACT: SOURCE** (official documentation), **FACT: DATA** (reproduced by the script), **INFERENCE**, **DECISION**.

---

## 1. The decision in one paragraph

**DECISION.** The primary business decision is made **at the level of the census metropolitan area (CMA)**: *where should SME acquisition effort be focused?*
- All 41 CMAs stay visible on one **reporting spine**, grouped into three evidence tiers.
- Markets are compared only where both business momentum and market scale exist: the 35 CMAs in Tiers 1 and 2.
- Each data source keeps its own **separate view**, with its own statistical unit and period.
- Industry and size are **supporting grains** inside each view.
- The 2023 ISED survey is **context**. It is never a market-size input, and it is never multiplied into cells.

No single table combines the three sources.

## 2. Geographic reporting spine and coverage tiers

**FACT: DATA** (`selected_reporting_spine.csv`, `geographic_coverage_tiers.csv`). The spine lists every CMA by its SGC 2021 code. The Phase E crosswalks give **24** Tier 1, **11** Tier 2 and **6** Tier 3 CMAs:

| Tier | Evidence | Role |
|---|---|---|
| 1 | Business momentum (StatCan 0722) + market scale and composition (StatCan 1097) + ISED financing context for the CMA | Primary decision set |
| 2 | Momentum + scale; ISED only at region or national level | Primary decision set; the missing CMA context is shown, never filled in |
| 3 | Scale and composition only. These CMAs became CMAs in 2021 and have no momentum series | Visible, but **never ranked alongside Tiers 1–2** |

- Every ISED CMA falls inside the 35 shared CMAs, so the tiers are clean.
- **Every cross-layer CMA view carries two caveats:**
  - the 2016 vs 2021 boundary flag from crosswalk E1;
  - the fact that ISED doesn't state its geography vintage.

## 3. Working SME definition

**DECISION.** An SME means an employer business with **1–499 employees**. Each measure is labelled with what it actually covers:

| Label | Meaning | Example |
|---|---|---|
| SME-location measure | 1097 locations with 1–499 employees (exact rollup) | SME employer locations in a CMA |
| Small-business measure | 0722 enterprises in the 1–4 and 5–19 bands | Small-business active enterprises |
| All-employer-business context | 0722 series for all sizes, including 500+ | CMA opening rate |
| Filtered SME survey measure | ISED estimate: 1–499 employees, at least $30,000 revenue, industry exclusions | Financing request rate |
| Diagnostic only | Approximate ISED-scope locations (crosswalk E6) | Never called "ISED-eligible SMEs" |

- **Why momentum is labelled "all-employer" (FACT: DATA):**
  - 0722 publishes openings for all five bands in **0** CMAs, so an SME-specific opening rate is impossible;
  - it publishes openings and closings for the 1–4 and 5–19 bands in only **22** CMAs;
  - in the CMAs that publish every band, 500+ enterprises are about **0.3%** of active enterprises, and the 1–19 bands are a median **86.3%** of active enterprises.

  **INFERENCE:** all-size momentum is therefore dominated by small firms. **DECISION:** it is still labelled all-employer-business context, never SME.
- **Other scope rules:**
  - 500+ is always shown separately as outside the SME scope.
  - Non-employer businesses are absent from every source.
  - ISED's revenue floor and eligibility rules cannot be reproduced on StatCan data.

## 4. Layer grains

**DECISION** (`layer_grain_decisions.csv`).

| Layer | Source and unit | Geography | Industry | Size | Period |
|---|---|---|---|---|---|
| **Momentum** | StatCan 33-10-0722, enterprises | CMA (2016 code bridged to 2021) | Business-sector total; sector view optional for 8 CMAs | All sizes; 1–4 and 5–19 optional | Monthly, to **2026-05** |
| **Scale and composition** | StatCan 33-10-1097, employer **locations** | CMA; municipality (CSD) detail left out of the MVP | 19 NAICS 2022 business sectors | 1–4, 5–19, 20–99, 100–499; 500+ separate | December 2025 |
| **Financing context** | ISED 2023, **survey estimates** | CMA (Tier 1), region, national | 10 ISED groups | 4 ISED bands | 2023 |

- **Rates:** 0722 rates always use 0722's own active enterprises as the denominator.
- **Shares:** 1097 shares use 1097 totals.
- **Locations** are never described as businesses or potential clients.

## 5. Measures

**DECISION** (`metric_eligibility.csv`): **25** metrics, **10** core.

**Core:**
- **Momentum (0722):**
  - active enterprises, openings and closures;
  - net formation, defined as openings minus closures;
  - opening, closure and net formation rates, each as a count ÷ the previous month's active enterprises.
- **Scale and composition (1097):**
  - SME locations (1–499);
  - sector share of classified SME locations;
  - size-band share of SME locations.

**Supporting or optional:**
- a 36-month momentum trend;
- small-business bands, with small-band opening rates only where published (22 CMAs);
- sector momentum (8 CMAs);
- CMA × sector × size locations.

**Contextual:**
- ISED financing-request, debt request and approval, and instrument rates;
- the weighted SME count;
- deal size, **national and size-band only**, because at CMA level Table 5 is **54.5%** F.

**Excluded from the CMA MVP:**
- exits and temporary closures: not published for CMAs, and they lag 6 months (latest **2025-11**);
- the E6 approximation, except as a diagnostic.

**ISED quality rules:**
- A–D estimates are shown.
- E estimates carry a caution flag and a sensitivity check.
- **F estimates are excluded.**
- `..` means unavailable, never zero.
- Two ISED segments are called different only when their gap exceeds about twice their combined standard error.
- **DECISION:** ISED stays contextual in the MVP. It covers only Tier 1 CMAs at CMA level, and it must not score markets.

## 6. Industry, size and time

**Industry** (`industry_scope_decision.csv`):
- **1097 NAICS sectors are the primary composition grain.** At SME level, **95.0%** of CMA × sector cells have 10 or more locations.
- **Shown separately:** public administration (91), which is outside the SME banking scope, and Unclassified.
- **Left out of the MVP:**
  - 4-digit industry groups, because they are sparse and hard to read;
  - ISED special aggregates and 0722 tourism members, because they overlap sectors.
- **0722 caveats:** [11] is excluded from cross-source views, and 0722's partial 61/62 appear only under their own labels.

**Size** (`size_scope_decision.csv`):
- The broad bands are 1–4, 5–19, 20–99 and 100–499, with 500+ separate.
- **1097** supports all bands for all 41 CMAs through an exact rollup.
- **0722** publishes active enterprises in all five bands in only **18** CMAs; openings and closings exist only for the 1–4 and 5–19 bands, in 22 CMAs. Where every band is published, the separately seasonally adjusted bands sum to the total within **0.924%**.
- **The full CMA × sector × size cross is optional, not core.** In the 100–499 band only **37.4%** of CMA × sector cells have 10 or more locations.

**Time** (`time_scope_decision.csv`). There is no common reference period.
- **0722:**
  - use the trailing 12 months ending **2026-05** (primary) and 36 months (supporting);
  - the pandemic window (March 2020 – December 2021) is flagged and left out of baselines, not deleted, because quality flags peak from March to July 2020;
  - sector detail starts in 2018-01.
- **1097** is "December 2025".
- **ISED** is "2023 survey".
- Every chart carries its own period label.

## 7. Analytical data model (specification only)

**DECISION** (`analytical_model_spec.csv`): **11** views, **5** fact tables, each fed by **one source only**:
- **Dimensions:** `dim_cma` (the spine), `dim_industry`, `dim_size_band`.
- **Facts:**
  - `fact_cma_momentum_monthly` (0722);
  - `fact_cma_market_scale_snapshot`, `fact_cma_sector_composition_snapshot` and `fact_cma_size_composition_snapshot` (1097);
  - `fact_ised_financing_context` (ISED, one-way segments).
- **Bridges:** `bridge_cma_vintage` (E1), `bridge_industry` (E3, E4), `bridge_size_band` (E5).

Every view has a code-based primary key, and no join uses names. The model is **not populated** in this phase.

## 8. Permitted and prohibited comparisons

**Permitted:**
- comparing CMAs within a single layer (for example, opening rates across the 35 CMAs, or SME sector shares across the 41);
- showing layers side by side for one CMA with unit and period labels;
- attaching ISED Tier 1 context to a CMA as a labelled attribute;
- showing regional ISED context for Tier 2 and Tier 3 markets, labelled as regional.

**Prohibited:**
- a single combined fact table;
- 0722 flows ÷ 1097 locations;
- ISED percentages turned into counts or multiplied across dimensions;
- ranking F estimates;
- name joins;
- ranking Tier 3 CMAs on momentum;
- calling all-size series "SME";
- calling locations businesses;
- turning public aggregates into leads;
- mixing public data with the later synthetic CRM layer.

## 9. Why the alternatives were rejected

**DECISION** (`candidate_grain_assessment.csv`). This uses 12 published criteria with weights, plus an unweighted view and a ±25% weight-sensitivity test. It is **a design-decision aid, not an opportunity score**.
- The selected option (F) scored **4.275**, against **3.75** for the next best. It stays the winner under every weight change and in the unweighted view.

| Candidate | Why not selected |
|---|---|
| A: 35-CMA spine | Its decision set is kept, but used alone it silently drops the six scale-only CMAs |
| B: 24-CMA all-source | Drops 11 CMAs with valid StatCan evidence just because survey context is missing |
| C: 41-CMA flat spine | Compares markets as if their evidence were equal |
| D: 18-CMA SME-band subset | No SME-specific opening or closing rate exists even there, and the reduced coverage would bias comparisons |
| E: small-business bands | Small-band flow rates cover only 22 CMAs; kept as an optional view |

## 10. Unresolved caveats

- **Boundaries:** CMA boundaries differ between 2016 and 2021 (E1 flags); ISED's geography vintage is unstated.
- **0722 industry questions:** the scope of [11]; partial 61 and 62; the undocumented NAICS vintage.
- **ISED open questions:** undocumented `..` cells; whether supplementary populations are in its total.
- **Momentum isn't SME-specific.** It is all-employer context.

## 11. What the next phase may build

The next phase may populate the 11 specified views from the validated caches and crosswalks, add within-layer data-quality checks, and produce the layer-specific measures listed above. **It may not:**
- combine the sources into one fact table;
- score or rank markets before the scoring design is agreed;
- create leads;
- touch the synthetic CRM layer.
