# Project setup and pipeline

Environment setup, the local dbt profile, and how to run the analytical pipeline end to end.

This document holds the operational detail that used to sit in the README. The README is the project argument;
this is the runbook.

> Synthetic CRM data, hypothetical rules, fictional bank. The public Statistics Canada and ISED inputs are real.

## Architecture

| Layer | Technology | Location |
|---|---|---|
| Source download, raw validation, cache and lineage, ISED workbook parsing, crosswalk validation | Python | `src/validation/`, `data/raw/`, `data/cache/`, `data/reference/crosswalks/`, `data/validation/` |
| Transformation: sources, staging, intermediate logic, dimensions, bridges, facts, tests, documentation | **dbt** | `models/`, `seeds/`, `tests/`, `macros/`, `dbt_project.yml` |
| Analytical warehouse | **DuckDB** | `data/warehouse/sme_banking.duckdb`, with schemas `reference`, `staging`, `intermediate`, `analytics` |
| Statistical exploration, candidate-indicator profiling, charts | Python | `src/analysis/`, `data/analysis/`, `outputs/` |
| Reconciliation baseline (kept unchanged) | Python | `src/analytics/09_build_analytical_views.py` → `data/processed/analytical_views/` |
| Semantic layer and report | **Power BI** | `dashboard/power_bi/` |

The dbt marts in the `analytics` schema are the authoritative analytical relations. They reproduce the 11 validated
analytical views exactly, and the market exploration reads from them. Details on lineage, model grains, tests and
reconciliation are in [dbt_transformation_layer.md](dbt_transformation_layer.md).

**Base models (schema `analytics`):**

- **Dimensions:** `dim_cma` (41 CMAs), `dim_industry`, `dim_size_band`.
- **Bridges:** `bridge_cma_vintage`, `bridge_industry`, `bridge_size_band`.
- **Momentum (0722):** `fact_cma_momentum_monthly`.
- **Scale and composition (1097):** `fact_cma_market_scale_snapshot`, `fact_cma_sector_composition_snapshot`,
  `fact_cma_size_composition_snapshot`.
- **Financing context (ISED):** `fact_ised_financing_context`.

**Each fact table comes from one source only** and carries its source, period and statistical unit on every row:

- **0722** counts enterprises. All-size momentum is all-employer context, not SME-specific.
- **1097** counts locations, not businesses.
- **ISED** gives survey estimates.

## Installation

The project uses two Python environments, both with **DuckDB 1.5.3**, so they can open the same warehouse file:

1. **Analysis environment** (Python 3.13), used by the Python scripts. Its packages are listed in
   `requirements-analysis.txt`.
2. **dbt environment**, isolated so that installing dbt can't change the validated analysis packages:

   ```bash
   py -3.11 -m venv .venv
   ```

   ```bash
   .venv/Scripts/python.exe -m pip install -r requirements.txt -c requirements-dbt-lock.txt
   ```

   `requirements-dbt-lock.txt` pins every transitive dependency of the validated dbt environment.

The pipeline script resolves the analysis interpreter in this order: the `ANALYSIS_PYTHON` environment variable,
then `%USERPROFILE%\anaconda3\python.exe`, then `python` on `PATH`. Set `ANALYSIS_PYTHON` if your interpreter
lives somewhere else.

## Local profile

dbt needs a profile. Copy the example into the repository root; `profiles.yml` is git-ignored. The profile contains
no credentials, only the relative warehouse path `data/warehouse/sme_banking.duckdb`. The pipeline script creates it
automatically if it is missing.

```powershell
Copy-Item profiles.example.yml profiles.yml
```

Run every dbt command from the repository root with `--profiles-dir .`.

## Source data

`data/raw/` is **not** committed: the 0722 table alone is 1.69 GB. `data/source_manifest.csv` records every raw
file with its SHA-256, byte size, source title, source URL and reference period, and pipeline step 1 verifies the
raw files against it.

Download the three sources from the URLs in the manifest to rebuild from raw. Statistics Canada revises its tables,
so a download made later than the original may not match the recorded hashes. That is a real revision, not a bug —
if it happens, refresh the manifest deliberately and note the change rather than forcing the old hash.

## Running the pipeline

```bash
powershell -ExecutionPolicy Bypass -File ./run_analytical_pipeline.ps1
```

The script works from any directory. It never downloads data or deletes files, and it stops with a nonzero exit code
at the first failure. It runs, in order:

1. Verify the raw files against `data/source_manifest.csv`, the cache lineage, every validation summary, the baseline
   analytical views and the market-exploration baseline.
2. `dbt debug` and `dbt seed`, which loads the reviewed crosswalks and code maps.
3. `dbt run` for staging, then intermediate, then marts.
4. `dbt test`: base analytical layer: 151 dbt tests (the market opportunity framework adds its own tests, run
   separately).
5. Reconcile the 11 marts with the baseline Parquet views: `src/validation/11_reconcile_dbt_marts.py`.
6. Run the market exploration from the dbt marts twice into scratch folders. Compare the runs with each other and
   with the recorded baseline, then refresh in place:
   `src/validation/12_verify_market_exploration_regression.py`.
7. The market opportunity framework (`-Scope Opportunity` runs only this part): preconditions, opportunity dbt models
   and tests, `src/analysis/13_analyze_market_opportunity_framework.py`, and
   `src/validation/14_validate_market_opportunity_framework.py`.
8. The CRM acquisition-process design validation (`-Scope CRMDesign` runs only this part): confirm that the
   analytical platform is current and that the market opportunity validation passed, then run
   `src/validation/15_validate_crm_design.py`.
9. The synthetic CRM data layer (`-Scope CRMData` runs only this part): generate the synthetic data (validated in a
   temporary folder, then published), build the CRM staging, intermediate and mart models, run the CRM dbt tests, run
   `src/validation/17_validate_synthetic_crm_data.py`, check two-run reproducibility and run the mutation tests.
10. The lead-prioritization and RM-routing layer (`-Scope LeadRouting` runs only this part): verify the platform
    state, the CRM design, the synthetic CRM data and the declared configuration hashes; load the configuration
    seeds; build the priority and routing models; run the tagged tests; run
    `src/analysis/18_analyze_lead_prioritization_and_routing.py`; run
    `src/validation/19_validate_lead_prioritization_and_routing.py`; run its mutation tests; and verify
    repeated-build reproducibility.
11. `dbt docs generate` (skipped by `-Scope CRMDesign`).

`-Scope Base` runs steps 1-6 and then the dbt documentation step.

### Scoped runs

```powershell
powershell -ExecutionPolicy Bypass -File ./run_analytical_pipeline.ps1 -Scope Opportunity
```

```powershell
powershell -ExecutionPolicy Bypass -File ./run_analytical_pipeline.ps1 -Scope CRMDesign
```

```powershell
powershell -ExecutionPolicy Bypass -File ./run_analytical_pipeline.ps1 -Scope CRMData
```

```powershell
powershell -ExecutionPolicy Bypass -File ./run_analytical_pipeline.ps1 -Scope LeadRouting
```

```powershell
powershell -ExecutionPolicy Bypass -File ./run_analytical_pipeline.ps1 -Scope AcquisitionIntelligence
```

### Individual commands

```bash
.venv/Scripts/dbt.exe build --profiles-dir .
```

```bash
.venv/Scripts/dbt.exe test --profiles-dir .
```

```bash
python src/validation/11_reconcile_dbt_marts.py
```

```bash
python src/analysis/10_explore_market_opportunity.py
```

```bash
.venv/Scripts/dbt.exe docs generate --profiles-dir .
```

```bash
.venv/Scripts/dbt.exe docs serve --profiles-dir .
```

## Power BI

`dashboard/power_bi/` holds the report and the build kit that produced it:

| File | Purpose |
|---|---|
| `SME Growth & Acquisition Intelligence.pbix` | The report: 5 pages over a 9-table model, 60 of the 61 specified visuals built |
| `data_manifest.csv` | The 8 CSV tables the model loads, with expected row counts |
| `data_model_guide.md` | Table roles, keys, relationships and the anti-fan-out design |
| `measures.dax` | All 83 measures as authored, grouped, each with its format and QA target |
| `measure_catalog.csv` | The same 83 measures as a machine-readable catalog |
| `visual_specification.csv` | The authoritative visual contract: 61 visuals across 5 pages |
| `power_bi_build_guide.md` | Step-by-step build instructions |
| `dashboard_qa_checklist.md` | Checks to run against the built report |
| `sme_acquisition_theme.json` | The report theme |
| `wireframes/` | Layout wireframes rendered from `visual_specification.csv` |

The model loads the committed CSVs under `data/analysis/` through a `DataRoot` parameter. After cloning, set
`DataRoot` to your own clone path in **Transform data → Manage parameters** before refreshing.

```bash
python dashboard/power_bi/render_wireframes.py
```

## Repository hygiene

The following are generated or machine-specific, and all are git-ignored:

- `target/`, `logs/` and `dbt_packages/`;
- the DuckDB warehouse and its WAL files;
- `.venv/`;
- `profiles.yml`;
- raw downloads and caches.
