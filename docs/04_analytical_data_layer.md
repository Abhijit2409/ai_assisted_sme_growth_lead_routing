# Analytical Data Layer (Phase G)

**Canadian SME Acquisition Strategy.** This document describes the analytical data layer built from the Phase F specification ([03_mvp_analytical_scope.md](03_mvp_analytical_scope.md)): what each view contains, how it was built, and how it was checked. It is a data layer only. No opportunity scores, market rankings, acquisition priorities, synthetic CRM data, routing rules or dashboards have been produced. Public aggregates are never turned into leads.

- **Views:** `data/processed/analytical_views/` (Parquet), described by `analytical_views_manifest.csv`.
- **Validation:** `data/validation/analytical_views/`.
- **Script:** both are written by `src/analytics/09_build_analytical_views.py`, which also checks that the numbers quoted here match its outputs.

Labels: **FACT: SOURCE** (official documentation), **FACT: DATA** (reproduced by the script), **INFERENCE**, **DECISION**.

---

## 1. What was built

**FACT: DATA** (`analytical_views_manifest.csv`). The layer has **11** views, exactly as specified in Phase F. Each fact view is fed by **one source only**.

| View | Type | Source | Unit | Rows | Grain |
|---|---|---|---|---|---|
| `dim_cma` | dimension | 1097 profile + E1, E2, E8 + Phase F spine | CMA | 41 | one row per SGC 2021 CMA |
| `dim_industry` | dimension | 1097 NAICS profile + E3, E4, E7 | classification | 495 | system × code |
| `dim_size_band` | dimension | E5 | classification | 6 | 5 bands + total |
| `fact_cma_momentum_monthly` | fact | StatCan 33-10-0722 | enterprise / ratio | 3,782,864 | CMA × month × size × industry member × measure |
| `fact_cma_market_scale_snapshot` | fact | StatCan 33-10-1097 | location | 123 | CMA × size scope |
| `fact_cma_sector_composition_snapshot` | fact | StatCan 33-10-1097 | location | 861 | CMA × NAICS sector (1–499) |
| `fact_cma_size_composition_snapshot` | fact | StatCan 33-10-1097 | location | 205 | CMA × broad band |
| `fact_ised_financing_context` | fact | ISED SFGSME 2023 | survey estimate | 1,497 | table × measure × one-way segment |
| `bridge_cma_vintage` | bridge | E1 | classification | 41 | 2016 ↔ 2021 CMA pair |
| `bridge_industry` | bridge | E3, E4 | classification | 63 | source ↔ NAICS pair |
| `bridge_size_band` | bridge | E5 | classification | 9 | 1097 band → broad band |

For every view, the manifest records:
- output path and build timestamp;
- row and column counts;
- primary key;
- sources, periods and statistical unit;
- file SHA-256 and a schema fingerprint;
- build-script version and validation status.

Every fact row carries its source, statistical unit, period and scope class. Views are written to a temporary folder first, validated, and only then moved onto the exact target files. A failed data gate leaves the previously published files untouched.

## 2. How the build stays safe

- **Upstream checks come first** (FACT: DATA):
  - Phases A–F must all have passed;
  - all 7 raw files must match `data/source_manifest.csv`;
  - both StatCan cache lineages must be current. They are only checked; a stale cache fails the run and is never rebuilt;
  - the ISED cell cache must match Phase C's cell and value-state counts.
- **Protected inputs are fingerprinted before and after the run:** the caches, crosswalks and Phase A–F outputs. Any change fails the run.
- **Joins use codes.** Every join is recorded in `join_register.csv`, and no code-keyed join uses a label or name.
  - **One declared exception:** ISED publishes no codes at all. Its stub text is looked up once, exactly and within its row context (for example, a "Quebec" CMA row versus a "Quebec" province row), in the crosswalk keys reviewed in Phase E (E2, E4, E8). From then on only the resulting codes are used.
  - A gate confirms that each ISED cell resolves exactly once, and that the Quebec province row never resolves to CMA 421.

## 3. Business momentum (StatCan 0722)

**FACT: DATA.** The lineage-checked cache holds **6,015,632** CMA rows. The view keeps the Phase F grain:
- the 35 CMAs;
- the business-sector total (T004) plus the 18 sector members;
- all 6 size scopes;
- the 4 measures published for CMAs (active, opening, continuing, closing).

**What was left out, and why:**
- **Reopening, entrants, temporary closures and exits:** excluded because no CMA values are published for them.
- **Tourism members, subsectors 311 and 312, and the second business-sector aggregate:** excluded because they overlap the sectors.

Every excluded row is counted in `scope_reconciliation.csv`, and the counts add back to the cache exactly.

**The view has **3,782,864** rows: **1,891,432** published and **1,891,432** derived.**

- **Published rows keep their source status.**
  - Of the published rows, **151,707** carry a value (2,723 flagged `E`, use with caution). **1,739,725** are suppressed (`x`), and **0** are not available (`..`) within this scope.
  - Unavailable values stay NULL and are never zero-filled.
  - Every published value, status and vector matches the cache.
- **Derived rows implement the Phase F core formulas**:
  - M04 net formation = openings − closures;
  - M05–M07 opening, closure and net formation rates, each as a count ÷ the previous month's active enterprises.

  How derived rows are built:
  - Each derived row has its own unit and formula, and uses inputs from the same series only.
  - "Previous month" means the previous calendar month. The first month (2015-01) therefore has no rate.
  - A derived value exists only when all its inputs are published. It inherits the `E` caution flag from any of them.
  - An independent recomputation of M04–M07 for the business-sector total at all sizes, in all 35 CMAs (19,180 values), matches exactly. The other series use the same SQL.
- **Labels (DECISION):**
  - All-size series are labelled *all-employer-business context*.
  - The 1–4 and 5–19 bands are labelled *small-business measure*.
  - 500+ is labelled *outside SME scope*.
  - Every row flags the pandemic window (2020-03 to 2021-12) and the 2016→2021 boundary caveat from E1.

## 4. Market scale and composition (StatCan 1097)

**FACT: DATA.** Three snapshot views cover all 41 CMAs. Each starts from a full grid, so a CMA × sector cell with no SME locations appears as a row; it doesn't disappear. All values are employer **locations**, not businesses or clients, labelled "December 2025" (REF_DATE 2025-07).

- **Scale.** The 41 CMAs hold **1,012,121** employer locations. They include **1,009,239** SME locations (1–499), and SME = total − 500+ exactly in every CMA.
- **Sector composition.** Each CMA has 21 rows: 19 business sectors, plus public administration (91) and Unclassified shown separately.
  - Sector shares (S03) use classified SME locations in the 19 business sectors as their denominator, and sum to 1 in every CMA.
  - The 41 CMAs hold **84,726** Unclassified locations. Unclassified is never allocated to sectors; its own share of SME locations is S05.
- **Size composition.** The 4 SME bands, plus 500+ shown separately with no share. The bands reconcile exactly to the scale view.
- **Zeros.** 1097 publishes no zeros. A cell with no row inside a CMA is zero by reconciliation, as proven in Phase B. **342** snapshot rows include at least one such cell. Each row counts its published and reconciled-zero cells, so nothing is filled in silently.

## 5. Financing context (ISED 2023)

**FACT: DATA.** The view has **1,497** ISED rows, taken from the Phase F core tables with one-way segments only:
- **Tables 1, 3, 4 and 49:** national, the 4 size bands, the 24 Tier 1 CMAs, 10 regions and provinces, and the 10 industry groups.
- **Table 5 (deal size):** national and size band only, because at CMA level it is 54.5% F.

The national row reproduces Phase C: **863,533** weighted SMEs and **11,210** respondents.

- **Quality rules:**
  - A–D estimates are usable.
  - E estimates carry a caution flag.
  - The **35** F estimates keep their published value for audit, but have no usable value.
  - `..` is unavailable, never zero.
- **Geography:**
  - CMA rows carry the caveat that ISED doesn't state its geography vintage.
  - Nested regions (Northern and Southern Ontario, Manitoba, Saskatchewan, Alberta) are flagged with their E7 rule.
  - Regional values are never copied or split to provinces.
- **Survey percentages are never converted to counts.**

## 6. Dimensions and bridges

- **`dim_cma`: the reporting spine.**
  - 24, 11 and 6 CMAs in Tiers 1, 2 and 3; 35 in the primary decision set.
  - Tier 3 has no momentum and no 2016 code.
  - Each row gives its province, its ISED region key and a plain-language description of its evidence coverage.
  - Ottawa–Gatineau spans two provinces, so it lists both region keys (`24|35`) and nothing is split between them.
- **`dim_industry`: **495** industry members across three systems.**
  - 445 NAICS 2022 members from 1097, 32 0722 members, and 18 ISED groups and aggregates.
  - Each has an `in_mvp` flag, a business-sector scope flag, and additive and overlap flags. Overlapping members are never additive.
  - All 28 E7 rules are linked and checked against their labels.
- **`dim_size_band`:** 1–4, 5–19, 20–99, 100–499, 500+ and the total.
  - It deliberately carries **no unit**: the same bands describe enterprises (0722, ISED) and locations (1097), so the unit is stated on every fact and bridge row instead.
- **Bridges:** the E1, E3/E4 and E5 crosswalks, as published.
  - The 6 target-only CMAs have no 2016 code.
  - Aggregate and non-reconcilable members have no target code.
  - Both are declared nullable key parts.
  - No bridge permits allocating values across one-to-many rows.

## 7. Declared deviations

**DECISION** (`declared_deviations.csv`). These choices go beyond, or narrow, the Phase F text. Each is recorded rather than applied silently.

| Item | Decision |
|---|---|
| ISED Table 50 | Phase F's I06 cites Tables 49/50. The **280** Table 50 cells are a region × industry cross, so they would break the one-way grain. I06 is served by Table 49 |
| Derived momentum rows | Stored in the momentum fact as `value_origin = derived`, each with its own unit and formula |
| ISED text lookup | ISED has no codes. The exact, context-qualified lookup to reviewed crosswalk keys is the only use of text, and it is gated |
| Band scope classes | 500+ is *outside SME scope (500+)*; 20–99 and 100–499 are *SME-specific measure* (active only, published in 18 CMAs) |
| Nullable key parts | `bridge_cma_vintage.cma_code_sgc2016` (6 target-only CMAs) and `bridge_industry.target_code` (aggregates) |

## 8. What this layer does not do

- It does not combine sources in one fact table, or divide 0722 flows by 1097 locations.
- It does not score, rank or prioritise markets, and no model or dashboard reads it yet.
- It does not create leads or synthetic CRM records. The later synthetic CRM layer must stay separate from these public-data views.

## 9. Reproduce

```bash
C:/Users/abhij/anaconda3/python.exe src/analytics/09_build_analytical_views.py
```

The script exits with status 1 if any gate fails. Per-check results are in `view_checks.csv`, and the full summary is in `phase_g_summary.json`.

---

## Phase H status

**Status: exploratory analysis complete.** Phase H describes these views without changing them: market scale, momentum, composition, financing context, evidence quality, and a register of candidate indicators for Phase I. See [05_external_market_exploration.md](05_external_market_exploration.md). No scores, weights or rankings were produced.

---

## dbt transformation layer

**Status: dbt marts are the authoritative analytical relations.** The 11 relations described above are now built by dbt, in the DuckDB warehouse `data/warehouse/sme_banking.duckdb` (schema `analytics`), with the same names, grains, columns and business rules. They reconcile exactly with the Parquet views produced by `src/analytics/09_build_analytical_views.py`, which is kept unchanged as the comparison baseline. The market exploration now reads the dbt marts. See [dbt_transformation_layer.md](dbt_transformation_layer.md).

---

## Market opportunity framework

**Status: built and validated.** The external market opportunity framework reads these dbt marts (scale, size composition and momentum; ISED only as context) and adds its own tagged dbt models and marts without changing any of the 11 relations. See [market_opportunity_framework.md](market_opportunity_framework.md).
