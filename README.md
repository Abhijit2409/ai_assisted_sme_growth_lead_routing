# Canadian SME Acquisition Strategy: analytical data platform

A portfolio project that helps a hypothetical Canadian business-banking team decide where to focus small and medium-sized enterprise (SME) acquisition. It uses validated **public** Canadian data:
- **Statistics Canada 33-10-0722-01:** employer-business openings and closures, monthly;
- **Statistics Canada 33-10-1097-01:** employer business counts by location, December 2025;
- **ISED Survey on Financing and Growth of SMEs 2023:** financing context.

**Important framing**
- The data are **public aggregates**. No row is an individual business, prospect or lead.
- Internal CRM data introduced later in the project will be **synthetic**. Hypothetical internal-process problems are illustrations, not claims about any real Canadian bank.
- ISED financing estimates are **contextual evidence**, never market-size inputs.
- **Tier 3 CMAs** (six markets that were census agglomerations in 2016) have scale and composition data only. They are not eligible for momentum-based comparison.
- No opportunity score, indicator weighting, market ranking, lead routing or dashboard exists yet.

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
4. `dbt test` (151 tests).
5. Reconcile the 11 marts with the baseline Parquet views: `src/validation/11_reconcile_dbt_marts.py`.
6. Run the market exploration from the dbt marts twice into scratch folders. Compare the runs with each other and with the recorded baseline, then refresh in place: `src/validation/12_verify_market_exploration_regression.py`.
7. `dbt docs generate`.

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
