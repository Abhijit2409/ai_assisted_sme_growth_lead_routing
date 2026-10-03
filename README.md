# Canadian SME Acquisition Strategy: analytical data platform

A portfolio project that helps a hypothetical Canadian business-banking team decide where to focus small and medium-sized enterprise (SME) acquisition. It uses validated **public** Canadian data:
- **Statistics Canada 33-10-0722-01:** employer-business openings and closures, monthly;
- **Statistics Canada 33-10-1097-01:** employer business counts by location, December 2025;
- **ISED Survey on Financing and Growth of SMEs 2023:** financing context.

**Important framing**
- The data are **public aggregates**. No row is an individual business, prospect or lead.
- Internal CRM data are **entirely synthetic** (a fictional bank, fictional businesses and staff). Hypothetical internal-process problems, SLAs, capacity values and outcomes are simulation settings, not claims about or benchmarks for any real Canadian bank.
- ISED financing estimates are **contextual evidence**, never market-size inputs.
- **Tier 3 CMAs** (six markets that were census agglomerations in 2016) have scale and composition data only. They are not eligible for momentum-based comparison.
- The market opportunity framework compares 35 CMAs on external evidence under transparent weighting scenarios. It is an external market signal, not a prospect list or an investment decision.
- The lead-prioritization and RM-routing framework is a **hypothetical rule set for the fictional bank**, evaluated on the synthetic CRM data. Its comparisons against the synthetic historical assignments are rule-adherence, counterfactual and operational-exposure measures only, never evidence of a causal effect on conversion, revenue, profitability or customer experience. No dashboard, machine-learning model or executive KPI mart exists yet.

## Architecture

| Layer | Technology | Location |
|---|---|---|
| Source download, raw validation, cache and lineage, ISED workbook parsing, crosswalk validation | Python | `src/validation/`, `data/raw/`, `data/cache/`, `data/reference/crosswalks/`, `data/validation/` |
| Transformation: sources, staging, intermediate logic, dimensions, bridges, facts, tests, documentation | **dbt** | `models/`, `seeds/`, `tests/`, `macros/`, `dbt_project.yml` |
| Analytical warehouse | **DuckDB** | `data/warehouse/sme_banking.duckdb`, with schemas `reference`, `staging`, `intermediate`, `analytics` |
| Statistical exploration, candidate-indicator profiling, charts | Python | `src/analysis/`, `data/analysis/`, `outputs/` |
| Reconciliation baseline (kept unchanged) | Python | `src/analytics/09_build_analytical_views.py` → `data/processed/analytical_views/` |

The dbt marts in the `analytics` schema are the authoritative analytical relations. They reproduce the 11 validated analytical views exactly, and the market exploration reads from them. Details on lineage, model grains, tests and reconciliation are in [docs/dbt_transformation_layer.md](docs/dbt_transformation_layer.md).

**Final models (schema `analytics`):**
- **Dimensions:** `dim_cma` (41 CMAs), `dim_industry`, `dim_size_band`.
- **Bridges:** `bridge_cma_vintage`, `bridge_industry`, `bridge_size_band`.
- **Momentum (0722):** `fact_cma_momentum_monthly`.
- **Scale and composition (1097):** `fact_cma_market_scale_snapshot`, `fact_cma_sector_composition_snapshot`, `fact_cma_size_composition_snapshot`.
- **Financing context (ISED):** `fact_ised_financing_context`.

**Each fact table comes from one source only** and carries its source, period and statistical unit on every row:
- **0722** counts enterprises. All-size momentum is all-employer context, not SME-specific.
- **1097** counts locations, not businesses.
- **ISED** gives survey estimates.

## Documentation

| Document | Content |
|---|---|
| [docs/02_data_sources_and_validation.md](docs/02_data_sources_and_validation.md) | Source validation and crosswalks |
| [docs/03_mvp_analytical_scope.md](docs/03_mvp_analytical_scope.md) | Analytical scope, reporting spine and grains |
| [docs/04_analytical_data_layer.md](docs/04_analytical_data_layer.md) | The 11 analytical relations and their rules |
| [docs/05_external_market_exploration.md](docs/05_external_market_exploration.md) | Descriptive market exploration and candidate indicators |
| [docs/dbt_transformation_layer.md](docs/dbt_transformation_layer.md) | dbt transformation layer, tests and reconciliation |
| [docs/market_opportunity_framework.md](docs/market_opportunity_framework.md) | Market opportunity framework: indicators, scenarios, scores, sensitivity and limitations |
| [docs/crm_acquisition_process_and_requirements.md](docs/crm_acquisition_process_and_requirements.md) | Hypothetical CRM acquisition process: statuses, routing, SLAs, capacity, duplicates, KPIs, requirements and synthetic scenarios |
| [docs/crm_data_model_and_dictionary.md](docs/crm_data_model_and_dictionary.md) | Logical CRM data model, entity catalog, field dictionary and logged design changes |
| [docs/synthetic_crm_data.md](docs/synthetic_crm_data.md) | Synthetic CRM data: generation, entities, coverage, dbt models, validation, mutation tests and reproducibility |
| [docs/lead_prioritization_and_routing.md](docs/lead_prioritization_and_routing.md) | Lead prioritization and capacity-aware RM routing: declared configuration, decision grain, leakage proofs, priority score and bands, eligibility, capacity, preferences, queues, worked examples, policy comparison, validation and limitations |
| [docs/acquisition_intelligence_and_kpis.md](docs/acquisition_intelligence_and_kpis.md) | Integrated acquisition intelligence: the five marts and their grains, how double counting is prevented, the conversion maturity rule, the KPI catalog, routing concentration risk, and limitations |

## Installation

The project uses two Python environments, both with **DuckDB 1.5.3**, so they can open the same warehouse file:

1. **Analysis environment** (Anaconda, Python 3.13), used by the Python scripts. Its packages are listed in `requirements-analysis.txt`.
2. **dbt environment**, isolated so that installing dbt can't change the validated analysis packages:

   ```bash
   py -3.11 -m venv .venv
   ```

   ```bash
   .venv/Scripts/python.exe -m pip install -r requirements.txt -c requirements-dbt-lock.txt
   ```

   `requirements-dbt-lock.txt` pins every transitive dependency of the validated dbt environment.

## Local profile

dbt needs a profile. Copy the example into the repository root; `profiles.yml` is git-ignored. The profile contains no credentials, only the relative warehouse path `data/warehouse/sme_banking.duckdb`. The pipeline script creates it automatically if it is missing.

```powershell
Copy-Item profiles.example.yml profiles.yml
```

Run every dbt command from the repository root with `--profiles-dir .`.

## Running the pipeline

```bash
powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1
```

The script works from any directory. It never downloads data or deletes files, and it stops with a nonzero exit code at the first failure. It runs, in order:
1. Verify the raw files against `data/source_manifest.csv`, the cache lineage, every validation summary, the baseline analytical views and the market-exploration baseline.
2. `dbt debug` and `dbt seed`, which loads the reviewed crosswalks and code maps.
3. `dbt run` for staging, then intermediate, then marts.
4. `dbt test`: base analytical layer: 151 dbt tests (the market opportunity framework adds its own tests, run separately).
5. Reconcile the 11 marts with the baseline Parquet views: `src/validation/11_reconcile_dbt_marts.py`.
6. Run the market exploration from the dbt marts twice into scratch folders. Compare the runs with each other and with the recorded baseline, then refresh in place: `src/validation/12_verify_market_exploration_regression.py`.
7. The market opportunity framework (`-Scope Opportunity` runs only this part): preconditions, opportunity dbt models and tests, `src/analysis/13_analyze_market_opportunity_framework.py`, and `src/validation/14_validate_market_opportunity_framework.py`.
8. The CRM acquisition-process design validation (`-Scope CRMDesign` runs only this part): confirm that the analytical platform is current and that the market opportunity validation passed, then run `src/validation/15_validate_crm_design.py`.
9. The synthetic CRM data layer (`-Scope CRMData` runs only this part): generate the synthetic data (validated in a temporary folder, then published), build the CRM staging, intermediate and mart models, run the CRM dbt tests, run `src/validation/17_validate_synthetic_crm_data.py`, check two-run reproducibility and run the mutation tests.
10. The lead-prioritization and RM-routing layer (`-Scope LeadRouting` runs only this part): verify the platform state, the CRM design, the synthetic CRM data and the declared configuration hashes; load the configuration seeds; build the priority and routing models; run the tagged tests; run `src/analysis/18_analyze_lead_prioritization_and_routing.py`; run `src/validation/19_validate_lead_prioritization_and_routing.py`; run its mutation tests; and verify repeated-build reproducibility.
11. `dbt docs generate` (skipped by `-Scope CRMDesign`).

`-Scope Base` runs steps 1-6 and then the dbt documentation step.

**Individual commands:**

```bash
.venv/Scripts/dbt.exe build --profiles-dir .
```

```bash
.venv/Scripts/dbt.exe test --profiles-dir .
```

```bash
C:/Users/abhij/anaconda3/python.exe src/validation/11_reconcile_dbt_marts.py
```

```bash
C:/Users/abhij/anaconda3/python.exe src/analysis/10_explore_market_opportunity.py
```

```bash
.venv/Scripts/dbt.exe docs generate --profiles-dir .
```

```bash
.venv/Scripts/dbt.exe docs serve --profiles-dir .
```

## CRM acquisition process design

This is a **hypothetical design for a fictional bank**. It specifies a target-state SME acquisition process, CRM requirements and control rules for later lead prioritization and relationship-manager routing. Nothing in it describes the internal process of any real Canadian bank, and the CRM data are entirely synthetic. No priority or routing score, RM recommendation, dashboard or machine-learning model exists.

- **Documents:** [docs/crm_acquisition_process_and_requirements.md](docs/crm_acquisition_process_and_requirements.md) and [docs/crm_data_model_and_dictionary.md](docs/crm_data_model_and_dictionary.md).
- **Design catalogs:** `data/design/crm/`. These include assumptions, stakeholder needs, channels, lead statuses and transitions, assignment statuses, routing, territory, specialist, capacity, SLA and duplicate rules, the entity catalog and field dictionary, KPIs, requirements, acceptance criteria, traceability, synthetic scenarios and volumes. They are reviewed design inputs, not dbt seeds and not raw data.
- **Validation:** `src/validation/15_validate_crm_design.py` writes its evidence to `data/validation/crm_design/`.

```powershell
powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope CRMDesign
```

## Synthetic CRM data

A deterministic synthetic operating year (2025-06-01 to 2026-05-31 UTC) of the hypothetical process: about 15,600 fictional business prospects, 17,400 leads, 140 fictional relationship managers and their assignments, activities, SLA clocks, capacity snapshots, routing decisions, duplicate groups and opportunities. Every record is synthetic; every rate is a simulation setting, never a benchmark.

- **Generator:** `src/synthetic/16_generate_synthetic_crm_data.py` (master seed 20260929), driven entirely by `data/design/crm` (including `synthetic_generation_parameters.csv` and `business_calendar.csv`).
- **Data:** `data/synthetic/crm/` (one Parquet file per entity, manifest with hashes); generator ground truth, for validation only, in `data/synthetic/crm_ground_truth/`.
- **dbt:** sources in `models/sources/_crm_sources.yml`, models in `models/staging/crm`, `models/intermediate/crm` and `models/marts/crm`, tests in `tests/crm`, all tagged `crm_data`.
- **Validation:** `src/validation/17_validate_synthetic_crm_data.py` (independent checks, mutation tests) with evidence in `data/validation/synthetic_crm/`.
- **Details:** [docs/synthetic_crm_data.md](docs/synthetic_crm_data.md).

```powershell
powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope CRMData
```

## Lead prioritization and capacity-aware RM routing

A transparent, **hypothetical** rule set for the fictional bank that answers: which eligible SME leads should receive
attention first, and which available RM or controlled queue should receive each lead? It is evaluated on the synthetic
CRM data at the grain of **one lead per routing-decision timestamp** (19,777 decisions over 17,400 leads). There is no
machine-learning model, conversion prediction, propensity score, revenue forecast, optimization solver, dashboard or
executive KPI mart.

- **Declared configuration:** ten seed files in `seeds/lead_routing/`, hashed before the first score was produced; the
  declaration and every logged change are in `data/validation/lead_routing/configuration_declaration.json`.
- **Priority:** eligibility is decided before scoring; eligible leads receive an explainable 0-100 score from a
  business-value pillar (0.65) and an operational-urgency pillar (0.35), with external market context capped at 0.078
  of the score. Bands P1 >= 65, P2 >= 45, P3 < 45. The source `priority_band` is preserved unchanged.
- **Routing:** five lead-level and nine candidate-level mandatory rules, then seven weighted preferences, then a
  four-level deterministic tie-break ending in a stable RM identifier. Capacity comes from the most recent snapshot at
  or before the decision; no ordinary recommendation is made at or above the hard limit. Nine controlled queues each
  carry an owner, SLA, next action and escalation path.
- **Two recommendations, kept apart.** `mart_routing_recommendation_static` ranks each decision independently and never
  consumes capacity: a **diagnostic** of rule fit, not an executable plan. `mart_routing_recommendation_operational` is
  the **operational** answer: it walks the decisions in a declared order and keeps a simulated RM capacity ledger, so
  each assignment consumes the chosen RM's capacity for every later decision and no assignment pushes an RM to or past
  the declared hard limit.
- **Honest result:** the ledger enforces capacity correctly within a snapshot window (it redirects 46 decisions and
  peaks at 38 of 40 workload points) but barely changes year-long workload concentration, because the capacity
  snapshots were produced by the historical policy and reset the ledger each night. This layer should not be presented
  as a workload-balancing solution.
- **Leakage:** no later event, outcome, `fct_opportunity_outcome` record, ground-truth label or historical selected RM
  enters any score or recommendation. Proved by transitive dbt manifest lineage, a column-level read check on the
  compiled SQL, independent recomputation and mutation tests.
- **Comparison with the synthetic historical assignments** is a rule-adherence comparison, a routing-policy
  counterfactual, an operational-exposure measure and an assignment-quality proxy. **It is never evidence of a causal
  improvement in conversion, revenue, profitability or customer experience.** Recommendations never consume capacity, so
  workload concentrates under the recommended policy; that result is reported, not hidden.
- **dbt:** 11 seeds, 11 intermediate models, 6 marts and 20 singular tests, all tagged `lead_routing` (168 tests). The
  base (151), opportunity (39) and synthetic CRM (383) test counts are unchanged.
- **Validation:** `src/validation/19_validate_lead_prioritization_and_routing.py`, 92 of 92 checks, 19 of 19 mutation
  tests (four of them injected into copies of the dbt manifest, the compiled SQL and the configuration seed, so the
  leakage proof itself is exercised), and 61 of 61 artefacts byte-identical across repeated runs — dbt relations,
  analysis files, figures and validation evidence; evidence in `data/validation/lead_routing/`.
- **Details:** [docs/lead_prioritization_and_routing.md](docs/lead_prioritization_and_routing.md).

```powershell
powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope LeadRouting
```

## Integrated acquisition intelligence

Connects the market opportunity framework, the synthetic CRM activity, funnel performance, lead priority,
operational RM routing, SLA performance and RM capacity so the fictional bank can ask: where should SME acquisition
effort go, which prospects need action, and where are leads delayed, lost or constrained by capacity? It adds no new
scoring and changes no opportunity, priority or routing weight.

| Mart | Grain |
|---|---|
| `mart_acquisition_market_focus` | one row per CMA at the planning snapshot (plus an explicit unresolved-geography row) |
| `mart_acquisition_funnel_monthly` | reporting month x CMA x channel x SME segment x industry grouping |
| `mart_rm_acquisition_performance_monthly` | relationship manager x reporting month |
| `mart_acquisition_action_queue` | one row per currently actionable lead |
| `mart_acquisition_kpi_snapshot` | one row per reporting cutoff date |

- **No double counting.** Status history reaches 12 rows per lead, SLA events 27, activities 19, assignments 6 and
  routing decisions 6. Every mart reads one intermediate model, `int_acq__lead_spine`, which is one row per lead, so
  a lead is counted once by construction. The executive snapshot reduces each KPI to a single value at its own grain
  before combining, so no fact table is joined to another at row level.
- **Maturity rule.** Conversion is reported only on cohorts given 90 days to convert (95% of observed conversions
  land within 93 days); recent leads are excluded from the denominator, not counted as failures.
- **No retrospective snapshot.** The monthly funnel carries no opportunity score; a test asserts it never appears.
- **24 KPIs** are defined in a machine-readable catalog seed with definition, formula, numerator, denominator,
  grain, filters, maturity rule, null handling, interpretation, limitation and stakeholder.
- **Headline:** assignment 99.5% of valid leads, contact 90.1% of assigned, qualification 27.8% of mature contacted,
  conversion 12.5% of mature valid. The funnel loses volume after the first conversation, not at intake.
- **Routing concentration risk, reported honestly:** the capacity-aware policy makes zero hard-cap breaches, yet
  72.31% of recommendations go to the busiest decile of RMs, Gini 0.773, 3,216 to one RM and 8 RMs receive none.
  Hard-cap compliance does not create balanced workload. The routing rules are deliberately unchanged here.
- **Validation:** `src/validation/21_validate_acquisition_intelligence.py` recalculates every important KPI from the
  source facts, 84 of 84 checks, 60 dbt tests, determinism verified across two runs.
- **Details:** [docs/acquisition_intelligence_and_kpis.md](docs/acquisition_intelligence_and_kpis.md).

```powershell
powershell -ExecutionPolicy Bypass -File .un_analytical_pipeline.ps1 -Scope AcquisitionIntelligence
```

## Limitations

- **Geography:**
  - CMA boundaries differ between SGC 2016 (0722) and SGC 2021 (1097), and they're aligned only through the reviewed bridge.
  - ISED doesn't state its geography vintage.
  - ISED regional values are never attached to CMAs.
- **Time:** 0722 runs monthly to 2026-05, 1097 is a December 2025 snapshot, and ISED covers calendar 2023. There is no common period.
- **Statistical units:** enterprises, locations and survey estimates are never combined in one table, and never used as each other's denominators.
- **Missing values:** suppressed and unavailable observations stay unavailable (NULL), never zero. A 1097 cell counts as zero only where reconciliation proves it.

## Repository hygiene

The following are generated or machine-specific, and all are git-ignored:
- `target/`, `logs/` and `dbt_packages/`;
- the DuckDB warehouse and its WAL files;
- `.venv/`;
- `profiles.yml`;
- raw downloads and caches.
