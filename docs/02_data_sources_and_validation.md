# Data Sources and Validation

Validation record for the real external sources used by the Canadian SME Acquisition Strategy project. Each source is validated before any market, industry, geography or size-band selection is made.

| Source | Status |
|---|---|
| Statistics Canada 33-10-0722-01 | **Validated: conditional pass** (Phase A, this document) |
| Statistics Canada 33-10-1097-01 | **Validated: conditional pass** (Phase B, this document) |
| ISED Survey on Financing and Growth of SMEs 2023 | **Validated: conditional pass** (Phase C, this document) |

**Evidence labels used in this document**

| Label | Meaning |
|---|---|
| **FACT: SOURCE** | Stated in the StatCan metadata file or official StatCan documentation |
| **FACT: DATA** | Demonstrated by querying the raw file. Reproducible by the validation script |
| **INFERENCE** | A reasonable interpretation of the data that no source document states |
| **PROJECT DECISION / ASSUMPTION** | A choice made by this project, not a property of the source |
| **DECISION** | A recommended project choice. Used in the Phase B section; same meaning as the label above |

---

## Statistics Canada 33-10-0722-01

### Source purpose

**FACT: SOURCE.** Title in the metadata: *"Experimental estimates for business openings and closures by employment size for Canada, provinces and territories, census metropolitan areas, seasonally adjusted."* Survey 5401, Monthly Business Openings and Closures (MBOC). Frequency: monthly.

**PROJECT DECISION / ASSUMPTION.** The project uses this table to measure **business-population dynamics**: where the stock of active employer businesses is, and where businesses are opening and closing. It is a candidate input to the question *"Where should SME acquisition effort be focused?"* It is not a count of all businesses (see Limitations).

### Raw files

| File | Bytes | Role |
|---|---|---|
| `data/raw/statcan_33100722/33100722-eng/33100722.csv` | 1,818,179,589 (1.69 GiB) | Data |
| `data/raw/statcan_33100722/33100722-eng/33100722_MetaData.csv` | 11,180 | Dimensions, members, footnotes, symbol legend |
| `data/raw/statcan_33100722/33100722-eng.zip` | 91 MB | Original download |

Raw files are read-only in this project. Byte size and modification time were checked before and after validation and were unchanged. Both files are valid UTF-8.

### Row count and date coverage

**FACT: DATA.**

| Item | Value |
|---|---|
| Rows | 8,575,672 |
| Reference periods | 137 months, 2015-01 to 2026-05 (no missing months) |
| Unit / scalar / decimals | `Number` / `units` / `0` for every row |
| Series (VECTOR) | 63,904, exactly one VECTOR per COORDINATE |
| Duplicate series-month rows | 0 |
| `SYMBOL` column | blank in every row (no preliminary/revised flags) |
| `TERMINATED` column | blank in every row |

### Dimensions

**FACT: SOURCE**, with counts confirmed as **FACT: DATA**.

| Dimension | Members | Notes |
|---|---|---|
| Geography | 49 | Canada, 10 provinces, 3 territories, 35 CMAs |
| Industry | 32 | Hierarchy from metadata `Parent Member ID` (see Industry classification) |
| Employment size | 6 | Total, 1–4, 5–19, 20–99, 100–499, 500+ |
| Business dynamics measure | 8 | See Measures |

`COORDINATE` is the concatenation of member IDs in the order geography.industry.size.measure. That lets us rebuild the **full theoretical grid**: 49 × 32 × 6 × 8 = 75,264 series, or 10,311,168 series-months. The file contains 8,575,672 of those rows.

**FACT: DATA. Name hygiene.** 44 of the 49 geography names end in a **non-breaking space (U+00A0)**, for example `"Alberta\u00a0"`. Only Canada, the three territories and Ottawa–Gatineau are clean. SQL `TRIM()` does not remove U+00A0.
→ **PROJECT DECISION:** join on `DGUID` or member ID, never on the `GEO` name.

**FACT: DATA.** DGUIDs use the **2016 geographic vintage** (`2016A…`, `2016S0503…`).

### Measures

**FACT: SOURCE** (metadata footnotes).

| Measure | Definition (summarised from metadata note) |
|---|---|
| Active businesses | One or more employees in the month (Note 4) |
| Opening businesses | No employees last month, at least one this month (Note 5) |
| Continuing businesses | At least one employee in both months (Note 6) |
| Closing businesses | Size basis: average employees over the prior 12 months (Note 11) |
| Reopening businesses | Openings that were active in some earlier month (Note 7) |
| Entrants | Not active in any month of the current or previous year (Note 8) |
| Temporary closures | Closures minus exits, so derived (Note 9) |
| Exits | Based on the LEAP definition and projected by regression. *"There are no published exits in the last six months"* (Note 10) |

Other notes that constrain use:
- **Note 2:** the unit is the enterprise. A multi-industry or multi-region enterprise is counted in each relevant industry and geography, so disaggregates *"may exceed totals."*
- **Note 3:** each series is seasonally adjusted separately.

### Status-code interpretation

| STATUS | Rows | % of rows | Rows with VALUE | Definition | Definition source |
|---|---:|---:|---:|---|---|
| `x` | 7,843,698 | 91.46% | 0 | *"suppressed to meet the confidentiality requirements of the Statistics Act"* | **FACT: SOURCE** (local metadata Symbol Legend) |
| blank | 627,970 | 7.32% | 627,970 | *"[Blank] or A-D: acceptable or better"* | **FACT: SOURCE** ([StatCan Standard table symbols](https://www.statcan.gc.ca/en/concepts/definitions/guide-symbol)). **Not defined in the local metadata.** |
| `..` | 94,108 | 1.10% | 0 | *"not available for a specific reference period"* | **FACT: SOURCE** (local metadata Symbol Legend) |
| `E` | 9,896 | 0.12% | 9,896 | *"use with caution"* | **FACT: SOURCE** (local metadata Symbol Legend) |

Notes on the codes:
- **FACT: DATA.** Blank STATUS is encoded in the raw file as a quoted empty string `""`, not a missing field. The earlier profile reported this as "NULL" because DuckDB's default reader turns quoted empties into NULL.
- **FACT: SOURCE.** The [StatCan CSV user guide](https://www.statcan.gc.ca/en/developers/csv/user-guide) says some STATUS symbols *accompany* a value and others *replace* it: *"When such symbols appear in the status field, the corresponding value field will remain blank."*
- **FACT: DATA.** This matches the file exactly. `x` and `..` **always** have an empty VALUE. Blank and `E` **always** have a VALUE. No row breaks the pattern.
- **FACT: DATA.** The local legend is StatCan's generic legend. Of its 14 symbols, only `x`, `..` and `E` occur in this table. `F`, `A`–`D`, `...`, `p`, `r`, `t`, `0s` and `<LOD` do not occur.

**Why VALUE is 92.6% null.** **FACT: DATA.** Nulls = `x` + `..` = 7,843,698 + 94,108 = 7,937,806. Every null is an explicit StatCan non-publication code. **There are no unexplained nulls.**

### Data availability findings

#### 1. Missingness is structural, not random

**FACT: DATA.** There are two distinct kinds of unavailability.

**(a) Rows absent from the file.** 1,735,496 rows of the theoretical grid are simply not published.
- 11,360 series (1,420 geography × industry × size blocks) are **entirely absent**. Absence is all-or-nothing: a block is missing for **all 8 measures or none**.
- Absent blocks are concentrated in the **500+** size band (794 of 1,420) and in CMAs (1,099). Canada has zero absent rows.
- A further 6,832 series (854 blocks) are present but omit some months. **All 6,832 contain zero values**, so this has no effect on usable data. No source document explains why these rows are omitted (see Unresolved).

**(b) Rows present but not published (`x`/`..`).** Each series was classified across its full history:

| Series class | Series | % of 75,264 |
|---|---:|---:|
| Absent from file | 11,360 | 15.09% |
| Never valued (all `x`, or `x` then `..`) | 58,894 | 78.25% |
| Partially valued | 1,559 | 2.07% |
| Always valued | 3,451 | 4.59% |
| **Any value at all** | **5,010** | **6.66%** |

**Evidence that this is a fixed publication design, not random gaps.** **FACT: DATA.**
- The number of valued cells is **identical in every month** within each period:

  | Period | Months | Valued cells per month |
  |---|---:|---:|
  | 2015-01 to 2017-12 | 36 | 3,705 (every month) |
  | 2018-01 to 2025-11 | 95 | 5,010 (every month) |
  | 2025-12 to 2026-05 | 6 | 4,756 (every month) |

- Every partially valued series has **one contiguous valued run** and follows **one of exactly two windows**:

  | Measures | Valued window | Series | Status outside window |
  |---|---|---:|---|
  | Active, Opening, Continuing, Closing | 2018-01 to 2026-05 (101 months) | 1,305 | `x` |
  | Exits, Temporary closures | 2015-01 to 2025-11 (131 months) | 254 | `..` |

- Measures are published in **fixed, nested groups**:
  - Opening, Continuing and Closing are published together in every block, with 0 exceptions.
  - The same holds for Reopening with Entrants, and for Exits with Temporary closures.
  - No block publishes Opening without Active.
- Per (geography, industry, size) block, 1,412 blocks publish Active only, 647 publish 4 measures, 7 publish 6, and 121 publish all 8.

**Conclusion (FACT: DATA).** The dataset does not have ordinary missing observations. StatCan publishes a fixed set of series. A series is either published for its whole window or not at all, and that set changed only at 2018-01 and at the Note 10 exit cutoff.

**FACT: DATA.** The smallest published value is ≥ 11 for every measure (≥ 281 for Continuing), and **no zeros are ever published**. Continuing businesses is published in exactly the same blocks as Opening and Closing (gate `measure_groups_published_together`), even though its values are far larger.

**INFERENCE.** The pattern fits confidentiality suppression applied at the series level, with small counts withheld. Continuing is likely suppressed alongside Opening because, with Active published, Opening could be derived from Continuing: this would be complementary suppression rather than a simple per-cell threshold. No source document found so far states the suppression rule or threshold.

#### 2. `..` and Note 10

**FACT: DATA.** `..` occurs **only** in Exits and Temporary closures, with 47,054 rows each.
- 94,108 − 120 = 93,988 `..` rows fall in the **last six months** (2025-12 to 2026-05). There, **every** Exits and Temporary-closures row is `..`. This matches Note 10 (no published exits in the last six months) and Note 9 (temporary closures are derived from exits).
- 120 `..` rows fall earlier (2023-08 to 2025-11). All of them are in series that are **never valued**, so they have no effect on usable data.

#### 3. Availability by dimension

All figures are **FACT: DATA**. Full tables are in `data/validation/33100722/`.

| Geography level | Expected series | % of rows in file with VALUE | % of full grid with VALUE |
|---|---:|---:|---:|
| Canada | 1,536 | 43.93% | 43.93% |
| Province (10) | 15,360 | 17.24% | 15.89% |
| Territory (3) | 4,608 | 4.78% | 3.10% |
| CMA (35) | 53,760 | 3.18% | 2.60% |

| Employment size | % with VALUE |
|---|---:|
| Total, all sizes | 13.74% |
| 1 to 4 | 12.85% |
| 5 to 19 | 8.92% |
| 20 to 99 | 1.28% |
| 100 to 499 | 1.47% |
| 500+ | 2.36% |

| Measure | Rows with VALUE | % with VALUE |
|---|---:|---:|
| Active businesses | 275,319 | 25.68% |
| Opening / Continuing / Closing (each) | 98,615 | 9.20% |
| Reopening / Entrants (each) | 16,714 | 1.56% |
| Temporary closures / Exits (each) | 16,637 | 1.55% |

| Industry class | % with VALUE |
|---|---:|
| Business-sector aggregates | 23.80% |
| NAICS sectors | 7.16% |
| Tourism aggregates | 6.56% |
| Tourism components | 3.91% |
| NAICS subsectors | 3.01% |

**Canada, provinces and CMAs differ sharply.** **FACT: DATA.**
- **Canada:** complete for the headline series (T004, all sizes). All 8 measures have values in every month, except Exits and Temporary closures, which have 131 of 137 months per Note 10.
- **Provinces:** 7 of 10 have all 18 NAICS sectors valued (Active businesses, all sizes).
- **Territories:** no values at all for sizes 20–99, 100–499 or 500+.
- **CMAs:**
  - All 35 have the business-sector total (T004) for Active, Opening, Continuing and Closing, **from 2015-01**. All 435 valued CMA T004 series start in 2015-01.
  - **No CMA has any value for Reopening, Entrants, Temporary closures or Exits.**
  - Size detail at T004: 18 CMAs have all 6 size bands; 17 have only Total, 1–4 and 5–19.
  - **NAICS sector detail exists for only 8 CMAs**, and **only from 2018-01**:

    | CMA | NAICS sectors with values (of 18) | Size bands at T004 |
    |---|---:|---:|
    | Montréal | 18 | 6 |
    | Toronto | 18 | 6 |
    | Vancouver | 18 | 6 |
    | Hamilton | 16 | 6 |
    | Winnipeg | 16 | 6 |
    | Kitchener–Cambridge–Waterloo | 15 | 6 |
    | Guelph | 15 | 3 |
    | Oshawa | 15 | 3 |

  - Calgary, Edmonton, Ottawa–Gatineau, Halifax, Québec and the other 22 CMAs have **no** sector-level values in this table.

#### 4. Availability over time

**FACT: DATA.**
- All 137 months contain usable values. No month is empty.
- The share with a value is about 5.95% in 2015–2017 and about 7.96–8.04% in 2018–2025. All 1,305 series that start at 2018-01 are **CMA-level industry detail**, in the 8 detail CMAs:
  - NAICS sectors: 912
  - Tourism: 247
  - The T004-excluding-61/62 aggregate: 111
  - NAICS subsectors: 35

  No Canada, province, territory or CMA-T004 series starts in 2018. The step is a publication-scope expansion, not a data-quality change.
- 2026 covers 5 months only (7.59%). Its lower share is the Note 10 exit window, not a quality drop.
- **`E` (use with caution)** is concentrated in **March–July 2020**, peaking at 2,396 rows in 2020-04. 7,264 of the 9,896 `E` rows fall in 2020.
  - **INFERENCE:** this coincides with the onset of COVID-19. The source does not state the cause.

#### 5. Industry classification

**FACT: SOURCE.** Taken from the metadata `Parent Member ID` hierarchy and footnotes. Full table: `industry_classification.csv`.

| Class | Count | Members | Caveats |
|---|---:|---|---|
| Business-sector aggregate | 2 | `[T004]` all business sector industries; T004 excluding NAICS 61 and 62 (Note 15) | These are totals. They overlap everything below. |
| NAICS sector | 18 | 11, 21, 22, 23, 31-33, 41, 44-45, 48-49, 51, 52+55, 53, 54, 56, 61, 62, 71, 72, 81 | **52+55 is one combined member.** **61 and 62 are partial:** Note 12 limits 61 to NAICS 6114–6117. Note 13 limits 62 to 621, 623 and 624, which excludes hospitals (622). |
| NAICS subsector | 2 | 311 Food manufacturing; 312 Beverage and tobacco | Parented to T004 in the metadata, **not** to Manufacturing 31-33, although they are part of it by NAICS code |
| Tourism aggregate | 2 | Tourism industry; Total tourism transportation | Note 14: *"Tourism is not an official industry under the North American Industry Classification System."* It is built from parts of several NAICS industries. |
| Tourism component | 8 | Air, rail, water, bus/taxi, travel services, recreation, accommodation, food and beverage (tourism) | Overlaps the NAICS sectors (for example, Accommodation (tourism) is inside sector 72) |

### Limitations

1. **Experimental estimates** (**FACT: SOURCE**, title). The methodology may change between releases.
2. **Not additive** (**FACT: SOURCE**, Notes 2 and 3):
   - Industries do not sum to T004 and CMAs do not sum to provinces, because an enterprise is counted in every industry and region it operates in.
   - Each series is seasonally adjusted separately.
   - Tourism members overlap the NAICS sectors.
3. **Employer businesses only** (**FACT: SOURCE**, Note 4). "Active" means one or more employees, so non-employer businesses are excluded.
4. **Heavy suppression** (**FACT: DATA**). Only 5,010 of 75,264 possible series (6.66%) have any value. Detail below the provincial level is thin.
5. **CMA industry detail** (**FACT: DATA**). It covers only 8 CMAs and starts only at 2018-01. CMAs have no Reopening, Entrants, Exits or Temporary-closures data.
6. **Exits lag** (**FACT: SOURCE**, Note 10). Exits and Temporary closures are unavailable for the latest six months, and this window should move forward with each release.
7. **Size basis differs by measure** (**FACT: SOURCE**, Note 11). For closures and exits, size means average employees over the 12 months *before* the event. It is not the current size.
8. **Geography vintage** (**FACT: DATA**). DGUIDs are 2016-vintage. Phase B established what this means against 33-10-1097-01 (see [Compatibility](#compatibility-with-33-10-0722-01)):
   - table 1097 uses SGC 2021 (its Note 7) and 2021-vintage DGUIDs;
   - all **35** CMA codes in this table also exist in 1097, and **six** CMAs exist only in 1097 (Fredericton, Drummondville, Red Deer, Kamloops, Chilliwack, Nanaimo) (`data/validation/33101097/cma_comparison_0722.csv`);
   - whether the shared codes cover the same boundaries is not established. Boundary reconciliation belongs to the later comparison and crosswalk work.
9. **Point-in-time snapshot** (**PROJECT ASSUMPTION**). The data reflects the download on disk, and later releases may revise values. `data/source_manifest.csv` (added in Phase B) records the sha256 of every raw file, so any change is detected. The original download date was not recorded and remains unavailable; the file time is the best proxy.

### Implications for this project

These are **constraints on later decisions**. No CMAs, industries or size bands have been selected.

- **Treat unavailable values as not published** (**PROJECT DECISION**). `x` and `..` are never zero-filled or imputed. Blank-status and `E` values are usable, and `E` should be carried as a caution flag.
- **Long-run trends from 2015** are possible at Canada and province level, and for the CMA business-sector total (T004) in all 35 CMAs. CMA industry detail starts only in 2018-01.
- **Any CMA × industry analysis** from this table is limited to 8 CMAs from 2018-01 onward. For the other 27 CMAs, this table supports only the business-sector aggregate.
- **CMA-level dynamics** are limited to Active, Opening, Continuing and Closing.
- **Size-band analysis** is reliable only for Total, 1–4 and 5–19 across most geographies. The 20+ bands are sparse and are absent in the territories.
- **Never sum** industries, CMAs or tourism components to build a total. Use the published aggregate.
- **Tourism members** are overlapping views, not additional sectors. They must not be treated as peers of the NAICS sectors.
- **Joins** use `DGUID`/member IDs, not `GEO` names (non-breaking spaces).
- **Any crosswalk** to 33-10-1097-01 must reconcile the 2016 DGUID vintage, the size bands and the NAICS granularity. That work is deferred to Phases D and E.

### Validation result

**CONDITIONAL PASS: suitable, with documented constraints.**

| Check | Result |
|---|---|
| File integrity (row count, no duplicates, one vector per series, single unit) | Pass |
| All metadata members resolve; no orphan coordinates | Pass |
| Every null VALUE explained by a StatCan status code | Pass |
| Status definitions sourced (not invented) | Pass (`x`, `..` and `E` from local metadata; blank from the official StatCan page) |
| Missingness demonstrated to be structural | Pass |
| All 137 months contain usable values | Pass |
| Fitness for fine-grained CMA × industry × size analysis | **Limited:** see Limitations 4–5 |

### Validation workspace (reproducibility)

| Item | Purpose |
|---|---|
| `src/validation/03_availability_statcan_33100722.py` | Phase A script. Runs 19 gates, including raw-to-cache lineage; all pass. It reports every gate, always writes `phase_a_summary.json`, and exits with status 1 if any gate fails |
| `src/validation/cache_lineage.py` | Shared cache-lineage helper for the Phase A and Phase B caches |
| `data/validation/33100722/*.csv`, `phase_a_summary.json` | Compact outputs. Commit these. |
| `data/cache/statcan_33100722.parquet` (16 MB) + `.lineage.json` | **Disposable cache**, not a source of truth: a single-scan Parquet copy of the raw CSV that keeps raw strings exactly and adds parsed member IDs and a numeric value. Gitignored. See cache lineage below |
| `data/cache/statcan_33100722_series.parquet` | Full-grid series table (75,264 rows), recomputed each run. Gitignored. |

**Cache lineage.** A cache is reused only if its lineage file shows that:
- it was built from the raw file whose sha256 is recorded in `data/source_manifest.csv`;
- its schema version is current;
- its columns are as expected;
- the cache file is byte-identical (sha256) to the one that passed parity.

Otherwise the cache is rebuilt. It is installed only after an independent parse of the raw CSV (Python `csv` module) and the cached raw fields give the same row count and the same order-independent content digest. The lineage file records:
- source path, sha256 and byte size;
- creation time and schema version;
- row counts and both content digests;
- the cache sha256.

The baseline profile (8,575,672 rows and the STATUS counts) is held in the Phase A script itself. The superseded profiling script and its root-level outputs were removed.

Run with the Anaconda interpreter, which has `duckdb` 1.5.3; the default Python 3.11 does not. The scripts derive the project root from their own location, so they run from any working directory:

```bash
C:/Users/abhij/anaconda3/python.exe src/validation/03_availability_statcan_33100722.py
```

### Unresolved questions

1. **Suppression rule.** The confidentiality threshold or method is not documented locally. The MBOC methodology may state it.
2. **Why 854 blocks omit some months.** It has no impact on usable data (0 values), but the cause is undocumented.
3. **Why Calgary, Edmonton and Ottawa–Gatineau lack sector detail** while smaller Guelph and Oshawa have it. The data cannot explain this; it is likely an MBOC publication or methodology choice.
4. **Retrieval date.** The source manifest was added in Phase B and checksum-protects every raw file. The original download date was not recorded and remains unavailable.
5. **Boundary reconciliation with 33-10-1097-01.** Phase B established the vintages (2016 here, SGC 2021 there), 35 shared CMA codes and six 1097-only CMAs (Limitation 8). Whether shared codes cover the same boundaries is left to the comparison and crosswalk work.

---

## Statistics Canada 33-10-1097-01 (Phase B)

Supporting outputs are in `data/validation/33101097/`; provenance is in `data/source_manifest.csv`. Every number below is written by `src/validation/04_validate_statcan_33101097.py`.

### Answers to the Phase B questions

| # | Question | Short answer | Detail |
|---|---|---|---|
| 1 | What does the table measure? | Counts of active business **locations with employees**, December 2025 | [Source purpose](#source-purpose-1) |
| 2 | Statistical unit? | The **statistical location**, not the enterprise used by 33-10-0722 | [Source purpose](#source-purpose-1) |
| 3 | Grains? | 41 CMAs and 3,345 CSDs (SGC 2021); NAICS 2022 down to 4-digit industry groups; Total + 8 size bands; one reference period | [Dimensions](#dimensions-and-hierarchy) |
| 4 | Why are values missing? | Nothing is suppressed. Cells with no row are zero by reconciliation: the arithmetic is FACT: DATA, and its premise is a supported INFERENCE. Provinces, Canada and small non-CMA CSDs are outside the table's scope and are unknown, not zero | [Value states](#status-codes-and-value-states) |
| 5 | Where is usable detail? | Every in-scope cell is determined. Counts are dense at CMA × sector × size and sparse (many 1s) at CSD × subsector or industry group | [Availability](#availability-and-density) |
| 6 | What reconciles? | Every testable hierarchy reconciles exactly: 6 tests, 940,642 parent cells, 0 differences. Province and Canada totals are not testable | [Reconciliation](#reconciliation-additivity) |
| 7 | Joinable to 33-10-0722? | **Not directly.** They can be linked only by 3-digit CMA code, and unit, geography vintage and period all differ | [Compatibility](#compatibility-with-33-10-0722-01) |
| 8 | Defensible market-size denominator? | **Yes** for location-based market scale by CMA/CSD × NAICS × size. **No** as the denominator for 33-10-0722 enterprise flow rates | [Compatibility](#compatibility-with-33-10-0722-01) |
| 9 | Limitations for the MVP grain | Location unit; single snapshot; no province/Canada totals; 4–13% Unclassified by CMA; many small counts | [Limitations](#limitations-1) |
| 10 | Suitable? | **CONDITIONAL PASS** | [Validation result](#validation-result-1) |

### Source purpose

**FACT: SOURCE (metadata).**
- **Title:** *"Canadian Business Counts, with employees, census metropolitan areas and census subdivisions, December 2025."*
- **Survey:** 1105 Business Register. **Frequency:** semi-annual.
- **Note 1:** businesses are counted by **statistical location**, so *"a retail business with 10 stores and a head office is counted 11 times."*
- **Note 2:** coverage is every CMA and its CSDs, plus CSDs outside CMAs that have 10 or more active businesses.
- **Note 3:** StatCan advises users not to use these data as a time series.
- **Note 4:** employment-size ranges should not be used to calculate employee totals.
- **Note 5:** NAICS 2022.
- **Note 6:** "Unclassified" means units not coded to an industry.
- **Note 7:** SGC 2021.

**FACT: SOURCE** ([Business Register data products guide](https://www.statcan.gc.ca/eng/statistical-programs/document/1105_D16_V4)):
- A location is a production entity operating at a single physical location that resides in the smallest standardized geographic area. It is distinct from the establishment and the enterprise.
- Employment is derived from payroll deductions using the *"second-highest monthly value from the last 12 months"*.
- For complex businesses, employment is *"first determined at the enterprise level"*, then distributed to establishments and locations.
- Counts are produced for June and December and released in August and February. StatCan *"recommends that these data not be used as a time series."*
- For custom data, the listed confidentiality measure for employment-size ranges is "None".

**FACT: SOURCE** ([table page](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3310109701)): release date 2026-02-13. This is consistent with the guide's February release of December counts. "December 2025" comes from the table title; the page shows no separate reference-period label.

**DECISION.** The project uses this table as a **market-scale** source: how many employer locations exist by geography, industry and size. It is **not** a count of potential clients, and it is not a flow measure.

### Raw files and provenance

| File | Bytes | Integrity |
|---|---:|---|
| `data/raw/statcan_33101097/33101097-eng/33101097.csv` | 188,914,158 | sha256 in manifest; identical to the member in the original zip |
| `data/raw/statcan_33101097/33101097-eng/33101097_MetaData.csv` | 200,580 | sha256 in manifest; identical to the member in the original zip |
| `data/raw/statcan_33101097/33101097-eng.zip` | 17,683,142 | sha256 in manifest |

**FACT: DATA.** The raw files matched the manifest before and after processing (gates `raw_files_match_manifest_before` and `raw_files_match_manifest_after`).

**Retrieval date: not recorded.** The zip's file time, 2026-09-27 05:04 (UTC−7), is the best available proxy. The manifest covers every file under `data/raw`, including 33-10-0722 and ISED. ISED provenance fields are left for Phase C.

### Integrity and parity

**FACT: DATA** (`phase_b_summary.json`; 48 gates, all pass):

| Check | Result |
|---|---|
| Rows | 1,050,274 = newline count − 1 (no embedded line breaks) |
| Cache vs raw | An independent Python `csv` parse matches the DuckDB cache: identical row count and an identical order-independent sha256 digest over all 16 raw fields |
| Encoding | Valid UTF-8 with BOM, LF line endings; no U+FFFD; **no non-breaking spaces** (unlike 33-10-0722) |
| Blank encoding | Every field is quoted; empties are written as `""`; 0 unquoted empty fields; 0 SQL NULLs in any of the 16 raw columns in the cache |
| Uniqueness | One VECTOR per COORDINATE; 0 duplicate cells |
| Units | `Number` / `units` / 0 decimals in every row |
| Codes | `COORDINATE` = geography.size.NAICS member IDs; every ID exists in the metadata |
| Labels | GEO and size labels equal the metadata names. Every NAICS label is the metadata name followed by `[code]` |
| Metadata notes | All 7 notes are parsed from the raw note lines. Note 1 contains HTML link quotation marks that are not escaped for CSV, so a CSV reader splits it and a field-based parser truncates it at "For example,". The summary keeps both raw and cleaned text. Gates confirm Note 1 is complete (statistical locations, the retail example, the Business Register guide link), Note 5 names NAICS 2022 and Note 7 names SGC 2021 |

### Reference period

- **FACT: SOURCE.** The table title, in the metadata and on the table page, says December 2025. The metadata start and end reference periods are both 2025-07-01, and frequency is semi-annual.
- **FACT: DATA.** Every row has `REF_DATE = 2025-07`.
- **INFERENCE.** `2025-07` is StatCan's code for the second half-year period and denotes the December 2025 counts. The sources consulted do not document this encoding.
- **DECISION.** Label the data "December 2025 counts" and align with 33-10-0722 at 2025-12. Sensitivity to 2025-07 is reported under [Compatibility](#compatibility-with-33-10-0722-01).

### Dimensions and hierarchy

**Correction to earlier expectations:** the NAICS dimension has **445** members, not 446 (the earlier count included the dimension-definition line). Employment size has **Total + 8 bands**, not 9 bands.

**Geography** (`geography_profile.csv`; **FACT: DATA** from metadata IDs, parent IDs and DGUIDs):

| Level | Members | DGUID form | Notes |
|---|---:|---|---|
| CMA | 41 | `2021S0503` + 3-digit CMA code | Includes 6 CMAs not in 33-10-0722: Fredericton, Drummondville, Red Deer, Kamloops, Chilliwack, Nanaimo |
| CSD inside a CMA | 579 | `2021A0005` + 7-digit CSD code | 548 have rows; 31 have none. 36 have fewer than 10 locations, so the Note 2 threshold does not apply inside CMAs |
| CSD outside CMAs | 2,766 | `2021A0005` + 7-digit CSD code | Minimum total is exactly **10** locations, consistent with Note 2 |
| "Other Census Subdivisions" | 1 | none | Grouping member only; no rows and no total |

- **No Canada, province or territory members exist.** CSD codes cover all 13 provinces and territories. PEI (11) and the three territories have CSDs only outside CMAs. Ottawa–Gatineau spans province codes 24 and 35.
- **Label hygiene** (**FACT: DATA**):
  - 167 labels are shared by 353 geography members, for example three CSDs named "Alberton".
  - 36 CSDs carry the same label as their parent CMA.
  - CMA 421 is labelled **"Quebec"**, while in 33-10-0722 "Quebec" is the *province*.
  - **DECISION:** join on DGUID and codes only, never on names.

**NAICS** (`naics_profile.csv`):
- **FACT: DATA.** The metadata hierarchy is flat below the top. `Total, all industries [1]` has two children: `Unclassified [2]` (Note 6) and `Sub-total, classified [3]`.
- All 20 sectors, 99 subsectors and 323 four-digit industry groups have the **same metadata parent**, `[3]`. Sector → subsector → industry group is therefore derived from the NAICS code prefix. This is authoritative by the NAICS 2022 standard; ranges 31-33, 44-45 and 48-49 are handled explicitly.
- The code hierarchy is complete: every group has its subsector, every subsector its sector, and none is childless.
- Sectors include **55** (separate) and **91 Public administration**.
- `[1]`, `[2]` and `[3]` are table pseudo-codes, not NAICS codes.
- 22 labels are reused across levels by 45 members, for example "Utilities" for both 22 and 221.

**Employment size** (`employment_size_profile.csv`): Total, 1–4, 5–9, 10–19, 20–49, 50–99, 100–199, 200–499 and 500+. **FACT: DATA:** the bands are contiguous with no gaps or overlaps.

### Status codes and value states

**FACT: DATA** (`status_and_value_states.csv`). The in-scope grid is 3,386 listed geographies × 9 sizes × 445 NAICS = **13,560,930 cells**.

| State | Cells | Share | Definition source |
|---|---:|---:|---|
| Published positive count (blank STATUS) | 1,050,274 | 7.745% | Blank = "acceptable or better" ([StatCan symbols](https://www.statcan.gc.ca/en/concepts/definitions/guide-symbol)). **Not defined in the local legend** |
| Published zero | 0 | 0% | none printed |
| `x` suppressed | 0 | 0% | local legend |
| `..` not available | 0 | 0% | local legend |
| `E` use with caution | 0 | 0% | local legend |
| `F` too unreliable | 0 | 0% | local legend |
| Omitted, **zero by reconciliation** | 12,510,656 | 92.255% | see below |
| Unexplained missing | **0** | 0% | |
| Out of scope (not in table) | unknown | n/a | Canada, provinces and territories; CSDs outside CMAs with fewer than 10 active businesses (Note 2) |

**Why omitted cells are zero, and what the proof rests on.**
- **FACT: DATA.** Every published parent equals the sum of its published children exactly, in every hierarchy (next section). Counts cannot be negative, so omitted children under a published parent must sum to 0, and each one is therefore 0. Every listed geography with rows has its top cell (Total × Total) published, and the 31 CSDs with no rows reconcile to zero within their CMAs. Applied level by level, this covers every omitted in-scope cell.
- **INFERENCE (premise).** The proof assumes each published parent counts *all* in-scope units, i.e. values were not removed from both a parent and its children. The evidence supports this:
  - 502,611 published cells (47.9%) equal **1**, so small values are not withheld;
  - no row carries a status code;
  - the Business Register guide lists no confidentiality measure for employment-size ranges.
- **DECISION.** Represent omitted in-scope cells as `zero_by_reconciliation`, kept separate from published counts. **Never zero-fill out-of-scope geographies.** They are unknown, not zero.

### Reconciliation (additivity)

**FACT: DATA** (`reconciliation_summary.csv`). Only mutually exclusive children were used, for the same period, unit and measure:

| Test | Parent | Children (why mutually exclusive) | Parent cells | Exact | Differences |
|---|---|---|---:|---:|---:|
| Size | Total, with employees | 8 bands: contiguous and non-overlapping | 334,464 | 334,464 | 0 |
| NAICS top | Total [1] | Unclassified [2] + Classified [3]: a unit either has a code or not | 20,976 | 20,976 | 0 |
| NAICS sectors | Classified [3] | 20 sectors partitioning coded units | 20,929 | 20,929 | 0 |
| NAICS subsectors | each sector | its subsectors, by code prefix | 159,695 | 159,695 | 0 |
| NAICS industry groups | each subsector | its 4-digit groups, by code prefix | 321,015 | 321,015 | 0 |
| Geography | each CMA | its CSDs: SGC 2021 partition; a location resides in one area | 83,563 | 83,563 | 0 |
| Province / Canada | none in table | n/a | **not testable** | | |
| "Other Census Subdivisions" | grouping member without a total | n/a | **not testable** | | |

In every test, no parent is missing where its children are published, and no published parent lacks children.

This is the **opposite of 33-10-0722**, whose Note 2 says an enterprise is counted in every industry and region it operates in. In 1097 each location sits in exactly one geography, industry and size band, so the table adds up by design (**INFERENCE**, consistent with Note 1 and the guide's definition of a location).

**Provincial totals cannot be built from this table:** summing CSDs undercounts, because Note 2 excludes CSDs outside CMAs with fewer than 10 active businesses. **The CMA is the only complete, additive geography above the CSD.**

### Availability and density

**FACT: DATA** (`availability_matrix.csv`, where a *block* is one geography × one NAICS level × one size band; `cma_comparison_0722.csv`). Availability is not the constraint: every in-scope cell is either a published count or a proven zero, down to CSD × 4-digit industry group × size band. What varies is **how sparse and how small** the counts are:

| Geography × NAICS level (Total size) | % cells published | % of published cells = 1 | % of published cells ≥ 10 | Median |
|---|---:|---:|---:|---:|
| CMA × sector | 100.00 | 0.12 | 95.37 | 292.5 |
| CMA × subsector | 93.05 | 5.22 | 72.84 | 31 |
| CMA × industry group | 82.25 | 11.65 | 52.48 | 11 |
| CSD in CMA × sector | 71.77 | 16.41 | 50.28 | 10 |
| CSD in CMA × industry group | 27.76 | 34.65 | 22.88 | 3 |
| CSD outside CMA × sector | 63.05 | 28.91 | 22.37 | 3 |
| CSD outside CMA × industry group | 12.98 | 57.02 | 5.03 | 1 |

At CMA × sector, density falls with size. The share of cells published is 99.39% for 1–4 and 93.54% for 50–99, but 50.00% for 500+, where 33.90% of the published cells equal 1.

**Industry and size detail for CMAs that lacked it in 33-10-0722** (**FACT: DATA**):
- All 41 CMAs have locations in **all 20 sectors** and **all 8 size bands**, and 214–319 of the 323 industry groups.
- The **27 shared CMAs with no sector values in 33-10-0722 in 2025-12**, including Calgary, Edmonton, Ottawa–Gatineau, Halifax and Québec, all have full sector and size detail here. This detail is a December 2025 **stock of locations**, not enterprise dynamics.

**Unclassified share** (**FACT: DATA**):
- By CMA: 4.29%–12.66%.
- Across the 41 CMAs combined: 8.37%, concentrated in the 1–4 band (11.96%) and 0.17% at 500+.
- Sector-level counts exclude these locations.

**Finest consistently usable grain.** Availability allows CSD × industry group × size band. Small counts, relevance and comparability with the other sources will set the MVP grain in Phase F; **no grain is selected here**.

### Compatibility with 33-10-0722-01

| Dimension | 33-10-0722-01 (Phase A) | 33-10-1097-01 (Phase B) | Consequence |
|---|---|---|---|
| Statistical unit | Enterprise, counted in each industry and region it operates in (Note 2) | Statistical location, counted once (Note 1) | Counts measure different things |
| Additivity | Not additive by design | Exactly additive (FACT: DATA) | Different aggregation rules |
| Geography | Canada, 10 provinces, 3 territories, 35 CMAs | 41 CMAs + CSDs; no Canada or provinces | Only CMAs are common |
| Geography vintage | 2016 DGUIDs (vintage not stated in the metadata) | SGC 2021 (Note 7); 2021 DGUIDs | CMA codes match, but boundaries may differ |
| NAICS | Version **not stated** in its metadata; 18 sector-level members (52+55 combined; 61 and 62 partial; "[11]" titled "Forestry, fishing and hunting"), 2 subsectors, tourism aggregates | NAICS 2022; 20 sectors (incl. 91), 99 subsectors, 323 groups, Unclassified | Needs an industry crosswalk; tourism is not constructible |
| Size bands | Total, 1–4, 5–19, 20–99, 100–499, 500+ | Total + 8 bands | Numerically, every 1097 band nests in one 0722 band, but the unit and employment basis differ |
| Size basis | For closures and exits, average employees over the prior 12 months (0722 Note 11). The basis for active businesses and openings is **not stated** | Payroll-derived, second-highest month in the last 12 months. Which unit's employment sets the band is **not stated** | Not like-for-like |
| Time | Monthly, 2015-01 to 2026-05, seasonally adjusted | One semi-annual snapshot (December 2025); not a time series (Note 3) | Align at 2025-12 |
| Measures | Flows (openings, closures, exits…) + active stock | Stock only | Different roles |
| Suppression | 91.5% `x`; smallest published value 11 | None; values ≥ 1; zeros omitted | Very different coverage |

**CMA codes** (**FACT: DATA**, diagnostic): all 35 CMA codes in 33-10-0722 exist in 1097, and 6 are 1097-only. Two shared codes have different labels: 421 ("Québec, Quebec" vs "Quebec") and 522 ("Belleville, Ontario" vs "Belleville - Quinte West").

**Magnitude diagnostic** (**FACT: DATA**, `cma_comparison_0722.csv`). This compares 1097 locations with 0722 active enterprises (T004, all sizes) across the 35 shared CMAs. The 1097 side is expressed on 0722's sector list:
- [11] → 113, 114, 115;
- 52 + 55;
- 61 → 6114–6117 (0722 Note 12);
- 62 → 621, 623, 624 (0722 Note 13);
- 91 excluded.

Whether T004 includes Unclassified is unknown, so the result is a band:

| 1097 aggregate ÷ 0722 active enterprises | 2025-12: min / median / max | 2025-07: min / median / max |
|---|---|---|
| Sector list, excluding Unclassified | 1.157 / 1.252 / 1.344 | 1.151 / 1.253 / 1.339 |
| Sector list, including Unclassified | 1.232 / 1.346 / 1.421 | 1.243 / 1.349 / 1.415 |
| All industries | 1.269 / 1.386 / 1.468 | 1.280 / 1.381 / 1.474 |

- In every shared CMA, 1097 counts exceed 0722 counts.
- The ratio **varies by CMA**. The sector-list ratio ranges from 1.157 (St. John's) to 1.344 (St. Catharines–Niagara).
- It also **varies by industry**: across the 8 CMAs with 0722 sector detail, sector medians run from 1.183 (manufacturing) to 1.637 (finance + management) (`sector_ratio_diagnostic_0722.csv`).
- Switching the alignment month from 2025-12 to 2025-07 changes the sector-list ratio by at most 0.023.
- **INFERENCE:** the differences may reflect any combination of unit, activity definition, seasonal adjustment, boundaries and industry scope. The data cannot separate these, and **no cause is claimed.**
- 0722's "[11]" remains an outlier (median 3.43 across 3 CMAs) even when restricted to 113–115, so its scope is undocumented. Codes 113–115 are at most 1.4% of any CMA's sector-list aggregate, which keeps the effect on the totals small.

**Determinations** (**DECISION** unless marked):
- **Direct join: no.** The tables can be linked only by 3-digit CMA code for the 35 shared CMAs, with the 2016/2021 vintage caveat. There is no province or Canada link, because 1097 has no such members.
- **Crosswalks needed (Phase E):**
  - CMA vintage (2016 → 2021; 6 new CMAs);
  - industry (0722's member list → NAICS 2022 codes, including 0722's [11] and T004 scope);
  - size bands (numeric roll-up 8 → 5, documented as not like-for-like).
- **Snapshot stock as the denominator for 0722 monthly flows: not defensible.** The unit, scope, seasonal adjustment and period all differ, and the location-to-enterprise ratio varies by CMA (1.157–1.344) and by sector (1.183–1.637). Using it would distort comparisons across CMAs and industries.
- **Defensible rates:**
  - 0722 flows ÷ 0722 active enterprises, from the same source;
  - composition shares within 1097 (sector, size, and Unclassified shares of a CMA's locations);
  - 1097 counts as a separate market-scale indicator.
- **Time alignment**, if the two tables are shown together: 1097 December 2025 ↔ 0722 2025-12, labelled as different units.

### Limitations

1. **Location, not enterprise or client** (FACT: SOURCE, Note 1). A multi-location business contributes several counts. Location counts therefore overstate the number of distinct businesses, by an amount this table cannot measure.
2. **One snapshot** (FACT: SOURCE, Note 3). There are no trends, and StatCan advises against time-series use.
3. **No Canada, province or territory totals** (FACT: DATA). Summing CSDs undercounts, because small non-CMA CSDs are excluded (Note 2); the size of that gap is unknown.
4. **Unclassified locations** (FACT: DATA). They are 4.29%–12.66% of locations by CMA and are missing from every sector-level count.
5. **Small counts** (FACT: DATA). 47.9% of published cells equal 1 and 85.6% are below 10. The guide warns that CSD counts are volatile between periods.
6. **Size basis** (FACT: SOURCE). Size is payroll-derived and smoothed over 12 months, and for complex units it is distributed from the enterprise. Which unit's employment defines the published band is not stated.
7. **Geography vintage** (FACT: SOURCE, Note 7). SGC 2021 differs from 33-10-0722's 2016 DGUIDs; boundary changes are not quantified.
8. **Scope includes public administration (91)** and 55 as a separate sector. 33-10-0722's business-sector total differs.
9. **Aggregate statistics only** (DECISION). A count of 1 is a published aggregate. It does not identify a business and must never be treated as a lead.

### Validation result

**CONDITIONAL PASS.** The table is internally complete, exactly additive and unsuppressed. It is suitable as a **location-based market-scale source** at CMA and CSD × NAICS 2022 × size. It is **not** suitable as a direct denominator for 33-10-0722 enterprise flow rates, and it provides no provincial or national totals.

| Check | Result |
|---|---|
| Provenance, manifest and zip identity; raw unchanged before and after | Pass |
| Cache parity with an independent parse (row count + content digest) | Pass |
| Every status code identified and defined from sources | Pass. Only blank occurs; its definition comes from the StatCan symbols page |
| Hierarchies profiled from IDs, parent IDs, codes and DGUIDs | Pass |
| Every testable hierarchy reconciles exactly | Pass (6 of 6) |
| Unexplained missingness | Pass (0 cells) |
| Comparability with 33-10-0722 | **Limited:** different unit, vintage, time grain and scope |

### Validation workspace (Phase B)

| Item | Purpose |
|---|---|
| `src/validation/00_build_source_manifest.py` | Creates or verifies `data/source_manifest.csv`. It never overwrites a recorded checksum; a mismatch exits with status 1 |
| `src/validation/04_validate_statcan_33101097.py` | Phase B validation. 48 gates, all pass; any failure exits with status 1 |
| `data/validation/33101097/*` | Compact outputs (10 files). Commit these |
| `data/cache/statcan_33101097.parquet` (7.7 MB) + `.lineage.json` | **Disposable cache**, gitignored and not a source of truth. It is reused only while its lineage is current, using the same mechanism as Phase A; otherwise it is rebuilt and verified against an independent parse |

The compatibility checks read the Phase A cache `data/cache/statcan_33100722.parquet`. Before using it, Phase B verifies the cache's lineage: it must have been built from the 0722 raw checksum in the manifest, with the Phase A schema version, and be unchanged since parity. If the lineage is not current, Phase B stops with status 1 and asks for the Phase A script to be run. A row count alone is not accepted.

```bash
C:/Users/abhij/anaconda3/python.exe src/validation/00_build_source_manifest.py
C:/Users/abhij/anaconda3/python.exe src/validation/04_validate_statcan_33101097.py
```

### Unresolved questions

1. **Size-band unit.** Is the published band based on the location's employment or the enterprise's? The guide does not say.
2. **33-10-0722 industry scope.** The exact scope of "[11] Forestry, fishing and hunting", whether T004 includes Unclassified, and the NAICS version are not documented in its metadata.
3. **CMA boundary change 2016 → 2021** for the 35 shared CMAs (for example Belleville → Belleville – Quinte West). An SGC concordance is needed (Phases D and E).
4. **Locations excluded by Note 2** (non-CMA CSDs with fewer than 10 active businesses) cannot be counted from this table.
5. **`REF_DATE` = 2025-07** for December 2025 counts is inferred, not documented in the sources consulted.
6. **Retrieval date** was not recorded at download; the zip file time is a proxy.

---

## ISED Survey on Financing and Growth of SMEs 2023 (Phase C)

Supporting outputs are in `data/validation/ised_2023/`, and provenance is in `data/source_manifest.csv`. Every FACT: DATA number below is written by `src/validation/05_validate_ised_sfgsme_2023.py`, which also checks that the key numbers in this section match its outputs. FACT: SOURCE numbers come from the cited official pages.

### Verified identity

| Item | Value | Evidence |
|---|---|---|
| File | `data/raw/ised_sme_financing_2023/sfgsme-efcpme-tables-2023-eng.xlsx` | disk |
| Official title | *Survey on Financing and Growth of Small and Medium Enterprises, 2023*: data tables | FACT: SOURCE (cover and title page) |
| Producer / publisher | Produced by **Statistics Canada** in collaboration with **ISED Small Business Branch**; published by ISED on the Open Government Portal | FACT: SOURCE (cover page; [ISED survey page](https://ised-isde.canada.ca/site/sme-research-statistics/en/survey-data-and-analysis/survey-financing-and-growth-small-and-medium-enterprises/survey-financing-and-growth-small-and-medium-enterprises-2023)) |
| Source URL | [Open Government Portal resource](https://open.canada.ca/data/en/dataset/c3c9f361-c7d3-4c87-8be0-64c2b62521ee/resource/56f10b18-2aeb-4734-b0ad-8450bdf25d08) "Tables: Survey on Financing and Growth of SMEs 2023"; its download URL ends in this exact filename | FACT: SOURCE |
| Size | **2,482,697 bytes**, identical to the size the portal lists (gate) | FACT: DATA |
| Format | XLSX (Office Open XML), no macros | FACT: DATA |
| Checksum | sha256 `ca980683…860ba` (full value in the manifest), unchanged before and after the run | FACT: DATA |
| Reference period | Calendar year 2023. Collection ran 2024-02-22 to 2024-06-24 | FACT: SOURCE ([StatCan survey record 2941](https://www23.statcan.gc.ca/imdb/p2SV.pl?Function=getSurvey&SDDS=2941)) |
| Release | Data released **February 20, 2025** (cover). Portal resource published 2025-04-17; data last updated 2025-06-24, which matches the workbook's internal modified date 2025-06-24 | FACT: SOURCE / DATA |
| File modified on disk | 2026-09-27 05:05 (UTC−7) | FACT: DATA |
| Retrieval date | **Not recorded.** The file time is the best available proxy | manifest |

**Manifest.** The ISED row previously had empty provenance fields. They were filled with the verified title, portal URL, reference period and a provenance note. The ISED checksum, all other rows and the "not recorded" retrieval date are unchanged.

### What the survey measures

**FACT: SOURCE** (StatCan survey record 2941; workbook Notes sheet).
- **Purpose:** the types of debt, lease and equity financing SMEs use, their recent financing attempts, growth, obstacles and owner characteristics.
- **Statistical unit:** the **enterprise**.
- **Sampling frame:** the Business Register, 901,516 units.
- **Target population:** enterprises with **1–499 employees** and **at least $30,000** gross revenue in 2023.
- **Excluded:**
  - non-profits, joint ventures, government, subsidiaries, and financing and leasing companies;
  - NAICS 22, 52, 55, 61 and 91;
  - NAICS 5321, 5324, 6214–6223, 6242 and 814110.
- **Industry classification:** NAICS 2022.
- **Sample:** a base sample of 22,084 plus supplementary lists (ICT, advanced manufacturing, advanced technology, agri-food, registered charities and non-profits, co-operatives, CSBFP users) gives **29,100** enterprises. Estimates come from **11,210** responses.
- **Response rate:** the workbook gives **51.1%** "as a percentage of in-scope businesses". The survey record gives 52.4% "for the main population". Both are official (FACT: SOURCE). **INFERENCE:** they refer to different bases.
- **Weighting:** sampling weights represent the target population.
- **Size definitions:** "SME" means 1–499 employees. **Neither source defines "small" versus "medium".**

### Workbook structure

**FACT: DATA** (`workbook_inventory.csv`).
- **Sheets:** **55** worksheets, all visible. There are 4 front sheets (Cover page, Title Page, Notes, Index) and **51** table sheets (Tables 1–50 and A).
- **No formulas.** A formula-preserving read and a cached-value read are identical cell for cell.
- **No** external links, pivots, charts, Excel table objects, macros, comments, data validations, hidden rows or columns, or freeze panes.
- **Defined names:** all **211** are hidden housekeeping (18 autofilter ranges and 193 filter-data entries for **11** saved custom views). There are no user named ranges.
- **Merged ranges:** **1,281**. None intersects a data area (they are in titles, headers and footnotes).
- **Conditional formatting:** **20** rules, all "cell equals `x`" shown in dark red. **No cell contains `x`**, so the rules never fire.
- **Every table sheet has the same layout:**
  - title in A1, survey question(s) in A2 and population notes in A5;
  - headers in rows 11–15 (merged and unmerged);
  - a **unit row 16** of value / Quality / SE-or-CV triplets;
  - a row stub, then footnotes.
- **The row stub is identical** (**132** rows) across Tables 1–49 and A. Table 50 has its own 20-row stub.
- **Storage and formats:**
  - Percentages are stored on a 0–100 scale, with no `%` number format and no display scaling.
  - No numbers are stored as text.
  - Female-ownership stub labels are **numbers** (0, 0.5, 1) formatted to display as 0%/50%/100%. They are read as displayed.
- **Source anomalies, recorded and not corrected:**
  - **Table 38** row 14 carries stray visible header text ("Latin America", "Europe", "China", "Other") above the succession-intention columns. The row-13 labels are the meaningful categories.
  - 4 Index titles differ from their sheet titles (Tables 14, 30, 38 and 50; for example, Index "Succession Planning" versus sheet "Legacy Planning").

### Table discovery

**FACT: DATA** (`table_inventory.csv`, `estimate_definitions.csv`).
- There are **51** statistical tables, one per table sheet, and the Index lists exactly these.
- Each table splits into column **blocks**, one per top-level header group: **108** in total. Blocks on the same sheet can have **different populations**. In Table 1, for example, the request rate uses all SMEs while the reasons for not requesting use only non-requesters.
- Blocks contain **617** measures. Each is a value column with its Quality and SE/CV columns; Table A has values only.
- Stable identifiers: `ISED_T01`…`ISED_T50` and `ISED_TA` for tables, `_Bnn` for blocks, and `_Mnnn` for measures.
- Every data column belongs to exactly one measure, and measure column ranges never overlap (gates).

### Row dimensions (segments)

**FACT: DATA** (`row_dimension_catalogue.csv`, `dimension_availability.csv`). Rows are **one-way segments**; only Table 50 cross-tabulates.

| Dimension | Categories | Notes |
|---|---|---|
| All SMEs | 1 | 1–499 employees (Canada total) |
| Employment size | 4 | 1–4, 5–19, 20–99, 100–499 |
| Region / province | 10 rows | 5 regions (Atlantic, Quebec, Ontario, Prairies, BC and Territories). Quebec, Ontario, Manitoba, Saskatchewan and Alberta are individually available. Ontario is split north/south. **Atlantic provinces are not separate; BC is combined with the territories** |
| CMA | **24** | Named only, with no codes |
| Industry, NAICS-based | **10** | Some groups combine sectors: 11+21, and 51+53+56+62+71 |
| Industry, special aggregates | 8 | ICT, KBI, tourism and its sub-categories. They **overlap** the sector groups (footnote), so they must never be added to them |
| Other characteristics | the rest | Rural/urban, export, growth, business age, innovation, technology, IP, procurement, co-operatives, clean technology, social enterprises, legal status; potential and rejected borrowers are definitional segments |
| Owner characteristics | 33 rows | Owner age, education, birthplace, female ownership, ownership demographics |
| **Revenue bands** | **none** | Absent from every table |

### Estimate types and denominators

**FACT: DATA** (`estimate_definitions.csv`). Of the 617 measures, **556** are percentages (unit `%`). They break down as follows:
- 545 are prevalence percentages;
- 6 are ratios of dollar totals;
- 4 are average **interest rates**;
- 1 is Table 49's share of total.

The remaining measures are:
- 20 average and 15 weighted-total dollar amounts;
- 5 averages of years or months;
- 5 medians;
- 15 weighted enterprise counts;
- 1 unweighted respondent count.

How to read them:
- **Percentages in Tables 1–48 are prevalence rates within the row segment**, among the population each question applies to (the sheet's A5 notes). They are **not counts of businesses**.
- The population changes by block: all SMEs, requesters, approved borrowers, those turned down, lessees, exporters and so on. Many questions allow **multiple responses**, so those categories are not additive.
- All **282** measures in the Core and Contextual tables have a hand-annotated population that cites its evidence. Measures in out-of-scope tables are explicitly marked "unresolved (not annotated)".
- **Table 1, reasons for not requesting:** the reasons sum to 100 and "Financing not required" (81.4) exceeds the non-request rate (50.7), so the base is **non-requesters** (FACT: DATA). The sheet note says the question is "applicable to all respondents", which conflicts with the data.
- **Table 49 "Percentage":** it equals the share of the All-SMEs weighted count for **126** of 132 rows. **6** special-population rows use a different, undocumented base:
  - ICT, co-operatives, clean technology and federal-procurement SMEs print 100;
  - charity and not-for-profit split the non-profit social-enterprise group.
- **Counts of businesses:** only **Table 49 and Table 50 weighted counts** estimate numbers of enterprises (**863,533** SMEs in the target population in total). Table 49's respondent counts (**11,210**) are sample support, not estimates.
- **Dollar values:** Table 5 totals are weighted sums of the largest request per SME; averages are per requesting SME. Table A reports averages and medians from **CRA data** for incorporated SMEs with complete tax data (89.8% of SMEs).

### Quality, suppression and reliability

**FACT: DATA** (`quality_markers.csv`, `dotdot_contexts.csv`). There are 78,789 numeric values. **77,489** of them carry a quality code and a printed CV or SE; the other 1,300 are Table A values, which have no quality codes.

| Marker | Cells | Meaning (source) | Treatment (DECISION) |
|---|---:|---|---|
| A–D | **70,200** | Excellent to acceptable (Notes sheet) | Use |
| E | **4,122** | Use with caution | Use only with a caution flag |
| F | **3,167** | Not reliable, do not use. **The value is still printed** | Exclude from analysis |
| `..` | **987** | **Not defined** in the workbook or the survey record. StatCan's standard meaning is "not available for a specific reference period" | Unavailable; never zero-fill or impute |
| `…` | 0 | "not applicable" (footnote), but never used in a data cell | n/a |
| blank | **100** | All in Table 50's region-rows × region-columns block | Structurally omitted combination |
| 0 | **3,501** | True zero and rounded zero cannot be told apart; 30 are definitional | Use as published; exclude definitional rows |

- **Which quality thresholds apply.** The two sources disagree on E and F: the workbook says E is CV 20–30% (SE 10–15), while the survey record says 20–25% (SE 10–12.5). **All 77,489 printed codes follow the workbook thresholds.** The survey record's thresholds would reproduce only **75,922** (FACT: DATA).
- **`..` contexts** (INFERENCE, rule-based):
  - **339** cells fall in segments where the measure is **not applicable by definition**. Examples: potential borrowers in request-conditional tables, exporters in "reasons for not exporting", unincorporated firms in the incorporated-only Table A, and businesses 2 years old or younger in 3-year growth.
  - **648** cells are unavailable for an **undocumented** reason, mostly small segments. This is consistent with suppression, but the source doesn't state it. The inert `x` formatting suggests the template anticipated `x` suppression, which never appears.
- **Reliability of Core estimates** (share of printed estimates by quality code):
  - Financing demand holds up at CMA level: Table 1 is **97.9%** A–D and Table 3 is **94.6%** A–D.
  - Instrument request and approval rates (Table 4) are mostly usable at CMA level, but **8.3%** of estimates are F.
  - **Deal size (Table 5) is not reliable below the national level.** At CMA level only **25.2%** of estimates are A–D and **54.5%** are F. By industry, **18.8%** are F.

### Reconciliation

**FACT: DATA** (`reconciliation_results.csv`). There are **35** tests. Every test uses only categories that are mutually exclusive, share the same population, unit and period, and are declared per test.

Tolerances allow for rounding: 0.5 per rounded count and 0.05 per percentage rounded to one decimal. The footnote "Figures may not add to totals due to rounding and/or because response categories are not mutually exclusive" was respected: multiple-response blocks were never summed.

Results: **34** consistent and **1** finding.
- **Table 49:** size bands, regions, Ontario north/south, Prairie provinces, rural/urban, legal status, business age and the 10 industry groups all sum to All SMEs within rounding, for both weighted counts and respondents. The 10 industry groups are therefore **exhaustive**.
- **Table 50 against Table 49:** they agree within rounding. A direct test shows that every Table 50 region column, summed over the 10 industry rows, equals Table 49's count for that region. So Table 50's "British Columbia" column includes the territories.
- **Rates and distributions that sum exactly within rounding, for every non-definitional row:**
  - Table 1: request + non-request = 100, and the reasons sum to 100;
  - Table 3: full + partial = overall approval, and all outcomes sum to 100;
  - Table 9: full + partial = overall approval;
  - Tables 16, 19 and 38: their distributions sum to 100;
  - Table 20: the four severity levels sum to 100 for every obstacle (1,820 checks).
- **Cross-source checks:**
  - 11,210 respondents match the Notes sheet;
  - the Notes sample of 29,100 matches the survey record;
  - Table A's 89.8% coverage is at most Table 49's incorporated share (91.7%).
- **Finding:** Table 49 "Percentage" is not a uniform share of total (126 of 132 rows; see above). This is a property of the source, not an error in the data.

### Relevance to the SME acquisition project

**DECISION** (`project_relevance.csv`): **6** Core, **19** Contextual and **26** out of scope. No table was classed as validation-only.

- **Core:**
  - Tables **49 and 50**: market size, as weighted in-scope SME counts. Table 50 holds the workbook's only two-way cells: size × region, size × industry and industry × region.
  - Table **1**: demand for external financing.
  - Table **3**: debt demand and approval.
  - Table **4**: demand and approval by product (mortgage, line of credit, term loan, credit card), which bears on routing to RM specializations.
  - Table **5**: deal size, but only at national or size-band level given its reliability.
- **Contextual:**
  - Tables 2, 6–18: provider mix, terms, uses, rejection reasons, leasing, trade credit, equity, government financing, approved amounts, business age and start-up or acquisition financing.
  - Tables 19, 20, 26 and 38: growth, obstacles, expansion intentions and succession.
  - Table A: financial scale.
- **Out of scope:** exports, procurement, technology, consultants, innovation and IP, social enterprises, and owner demographics.
- **Usable grain:** one-way segments only. The workbook cannot supply CMA × industry × size cells.
- **DECISION: owner characteristics must never drive targeting or lead routing.** This covers owner age, education, birthplace, female ownership and ownership-demographic segments.
- **DECISION: survey percentages describe segments; they never identify businesses.** They must not be turned into prospect lists or leads.
- **Reference-year alignment:** 2023 is adequate for structural context, but it is about two years older than table 1097 (December 2025).

### Preliminary compatibility with 33-10-0722 and 33-10-1097 (diagnostic)

**FACT: SOURCE / DATA** (`compatibility_observations.csv`, `cma_label_diagnostic.csv`). The full comparison belongs to Phase D, and no crosswalks were built.

| Dimension | ISED 2023 | Compared with 0722 and 1097 |
|---|---|---|
| Statistical unit | Enterprise | Same as 0722; not 1097's locations |
| Population | 1–499 employees, ≥ $30k revenue, industry and ownership exclusions | Narrower than both; totals are not comparable without scope alignment |
| Size bands | 1–4, 5–19, 20–99, 100–499 | **Labels identical to 0722's**; 1097's 8 bands nest numerically |
| Industry | NAICS 2022; 10 coarse groups and overlapping aggregates | Coarser than both; needs a group-level crosswalk (Phase E) |
| Geography | Regions, 5 provinces, **24** CMAs by name, no codes | **23** of 24 CMA labels match 1097 after light normalization (accents, hyphen spacing); "Sudbury" does not (1097 uses "Greater Sudbury"). A name crosswalk is needed; no alias map was built |
| Period | 2023 (annual) | 0722 is monthly; 1097 is December 2025 |
| Estimate type | Weighted survey estimates with sampling error | Different from 0722's administrative estimates and 1097's register counts |

### Limitations

1. **One-way segments.** The table can't provide CMA × industry × size detail; Table 50 has only size, region and industry crosses.
2. **Filtered population.** The weighted counts cover only in-scope SMEs, not all employer businesses.
3. **Survey sampling error.** 3,167 printed estimates are F ("do not use"), and deal-size estimates are weak below the national level.
4. **`..` is undocumented.** 648 cells can't be attributed to a cause.
5. **Coarse geography.** Atlantic provinces are aggregated, BC includes the territories, and CMAs are identified by name only.
6. **No revenue bands**, and no definition of small versus medium.
7. **Reference year 2023** only; there is no time series in this workbook.
8. **Special populations.** Supplementary samples (for example non-profits) appear in some segments. Whether they affect the All-SMEs total is undocumented.

### Validation result

**CONDITIONAL PASS.** The workbook's integrity and structure are fully verified, and its estimates reconcile.

| Use | Suitable? |
|---|---|
| Estimating market scale | **Partly.** A survey-based count of in-scope SMEs (863,533) by one-way segment and three two-way crosses; not a register count |
| Comparing geographic markets | **Yes, one-way** at region, 5 provinces and 24 CMAs for demand and approval rates (A–D quality at CMA level). Rank two segments only when their difference exceeds about twice their combined standard error (printed per estimate) |
| Comparing industries | **Yes, one-way** at 10 coarse groups |
| Comparing SME-size segments | **Yes** (4 bands, identical to 0722's) |
| Understanding business needs and financing behaviour | **Yes.** This is the workbook's main strength |
| Supplementing 0722 and 1097 | **Yes, as segment attributes** (propensity, approval, national deal size); not joinable at cell level |
| Contributing to the opportunity framework | **Yes, as a contextual or propensity input** at segment level, with quality filtering; never as prospect counts |

### Validation workspace (Phase C)

| Item | Purpose |
|---|---|
| `src/validation/05_validate_ised_sfgsme_2023.py` | Phase C validation. Any failed gate exits with status 1 |
| `src/validation/00_build_source_manifest.py` | Now also fills verified provenance into empty manifest fields; never overwrites checksums |
| `data/validation/ised_2023/*` | 12 compact outputs: inventories, estimates, markers, reconciliation, relevance, compatibility and summary |
| `data/cache/ised_sfgsme_2023_cells.parquet` | **Disposable** tidy cell table (all 79,876 value cells, with dimension classes and `..` contexts) for later phases; gitignored |

```bash
C:/Users/abhij/anaconda3/python.exe src/validation/00_build_source_manifest.py
C:/Users/abhij/anaconda3/python.exe src/validation/05_validate_ised_sfgsme_2023.py
```

### Unresolved questions

1. **Meaning of `..`.** It is not defined in the workbook or the survey record, and 648 cells are unexplained.
2. **Supplementary populations.** Does the All-SMEs weighted count (863,533) include the supplementary-list populations, such as non-profits? What base do the 6 special-population rows of Table 49 use? **INFERENCE:** the printed weighted counts for not-for-profit and charity social enterprises total **940** (**0.11%** of the All-SMEs count), so including or excluding them cannot materially change the market scale.
3. **Response rate:** 51.1% (workbook) versus 52.4% (survey record). The bases appear to differ.
4. **Quality thresholds:** the workbook and the survey record conflict on E/F. The data follow the workbook.
5. **Small versus medium:** not defined by either source.
6. **Table 26:** is the market base all SMEs or those intending to expand?
7. **Source-note inconsistencies:**
   - Table 14's note refers to 2020;
   - Table 1's note calls Q47 "all respondents";
   - Table 7's note cites Q37 while its question text is Q32;
   - Table 38 has stray header labels.
8. **CMA geography:** the vintage and boundaries are not stated; "Sudbury" versus "Greater Sudbury".
9. **Retrieval date** was not recorded.

---

## Source grain comparison (Phase D)

Outputs are in `data/validation/source_comparison/`, written by `src/validation/06_compare_source_grains.py`. The script reads only the validated Phase A–C outputs. It rechecks the upstream gates, every raw checksum and both cache lineages before comparing anything. Every FACT: DATA number below comes from its outputs (`phase_d_summary.json`) or from the named upstream file. No crosswalk was built, and no MVP grain was chosen.

### Side-by-side grain comparison

**FACT: SOURCE / FACT: DATA** (`source_grain_matrix.csv`, `population_scope_matrix.csv`, `temporal_alignment_matrix.csv`).

| Dimension | 33-10-0722-01 | 33-10-1097-01 | ISED SFGSME 2023 |
|---|---|---|---|
| Statistical unit | Enterprise, counted in every industry and region it operates in (not additive) | Statistical location, counted once (exactly additive) | Enterprise; a weighted survey estimate |
| Population | Active employer enterprises, all sizes including 500+; business-sector total | Employer locations in all industries, including public administration (91) and Unclassified | Enterprises with 1–499 employees and at least $30,000 revenue; excludes NAICS 22, 52, 55, 61, 91 and parts of 53/62/81; excludes non-profits, government, subsidiaries, and financing and leasing companies |
| Time | Monthly, 2015-01 to 2026-05, seasonally adjusted | One snapshot: December 2025 counts; not a time series | Calendar year 2023 |
| Geography | Canada, 10 provinces, 3 territories, 35 CMAs; 2016 codes | 41 CMAs and their CSDs, plus CSDs outside CMAs; SGC 2021 codes; no provinces | Canada, 5 regions, 5 individual provinces, 24 CMAs by **name only** |
| Industry | 18 sector-level members (vintage not stated); 2 subsectors; 10 overlapping tourism members | NAICS 2022: 20 sectors, 99 subsectors, 323 groups, and Unclassified | NAICS 2022: 10 coarse groups and 8 overlapping aggregates |
| Size | 1–4, 5–19, 20–99, 100–499, 500+ | 8 bands, 1–4 through 500+ (location-based) | 1–4, 5–19, 20–99, 100–499 |
| Estimates | Enterprise counts: stock and flows | Location counts (stock) | Survey percentages, dollars and weighted counts, with CV/SE quality codes |
| Missingness | Administrative suppression (`x`); exits lag 6 months | None; omitted cells are zero | Sampling error (A–F); `..` partly undocumented |
| Identifiers | Member IDs and 2016 DGUIDs | Member IDs, 2021 DGUIDs and NAICS codes | **No codes** |

**The populations are not equivalent** (**DECISION**).
- Only ISED applies an employment ceiling, a revenue floor and organizational-form exclusions.
- Only 1097 includes public administration.
- Revenue and organizational form **cannot be reconstructed** from the StatCan tables.

**The three periods are never one observation period.**
- Each source supports trends only within itself: 0722 from 2015-01, with CMA sector detail from 2018-01; 1097 and ISED have one period each.
- For side-by-side presentation (**DECISION**), pair 0722 at 2025-12 with 1097 December 2025, show ISED 2023 as labelled context, and label every panel's period.
- No source may serve as another source's denominator.

### Geography, industry and size alignment

**FACT: DATA** (`dimension_alignment_matrix.csv`, `join_feasibility_matrix.csv`).

- **CMAs:**
  - There are **35** CMAs in 0722, **41** in 1097.
  - All 35 codes are shared, and **6** only in 1097 (Fredericton, Drummondville, Red Deer, Kamloops, Chilliwack, Nanaimo).
  - The codes are 2016 vintage in 0722 and SGC 2021 in 1097. **The effect of boundary changes is unknown**; for example, Belleville became "Belleville - Quinte West".
- **ISED labels:**
  - There are **24** ISED CMA labels, **23** of which match 1097 after light normalization.
  - "Sudbury" does not match; 1097 uses "Greater Sudbury".
  - "Quebec" is ambiguous. It is CMA 421 in 1097 and in an ISED CMA row, but the province in 0722 and in the ISED region rows.
  - **Names are never join keys.**
- **Local detail:**
  - CSD structure exists only in 1097.
  - 0722's industry detail covers only **8** CMAs, while 1097 has all sectors and size bands for **41** of 41 CMAs.
- **Industry:**
  - 0722's members need an explicit NAICS 2022 mapping: 52+55 are combined, 61 and 62 are partial, and the scope of [11] is undocumented.
  - ISED's 10 groups combine sectors.
  - 0722's tourism members, ISED's special aggregates and 1097's Unclassified **cannot be forced into a mutually exclusive NAICS hierarchy**.
- **Size:**
  - ISED's band labels are identical to 0722's for 1–499, and both are enterprise-based.
  - 1097's 8 bands nest numerically into the broader bands. **Numeric nesting does not make the counts equivalent**, because the unit is a location and the employment basis differs.
- **Units:** 1097 locations exceed 0722 enterprises by a varying factor: **1.157–1.344** across CMAs (sector list, excluding Unclassified) and **1.183–1.637** across sectors (Phase B).

### Role of each source

**DECISION**, tested by `layer_evidence.csv`: **9** of 9 evidence checks support it.

| Layer | Source | Evidence | Measures |
|---|---|---|---|
| **Momentum** | 33-10-0722 | Active, openings and closures are published for all **35** CMAs from 2015-01 | Active enterprises, openings, closures, net formation, opening and net-formation rates on its own denominator, rolling trends |
| **Scale and composition** | 33-10-1097 | **41** of 41 CMAs have every sector and size band; no unexplained missing cells | Employer locations, sector and size composition, CMA and CSD structure |
| **Financing context** | ISED 2023 | CMA demand estimates are **97.9%** (Table 1) and **94.6%** (Table 3) A–D. Deal size is not usable at CMA level (**54.5%** F) | Financing request and approval rates, product demand, national or size-band deal size, growth and obstacles |

**Separation is itself supported by the evidence.**
- The location-to-enterprise ratio varies, so 1097 cannot be the denominator for 0722.
- ISED has no codes, so it cannot be joined at cell level.

ISED percentages remain **segment attributes, never prospect counts**. The in-scope SME total (**863,533**) is a survey estimate, not a register count. Sales-process measures (lead volume, conversion, RM capacity) will come only from the future synthetic CRM data.

### Permitted and prohibited combinations

**DECISION** (`analytical_use_rules.csv`): **23** use rules, of which **14** are prohibited.

| Class | Uses |
|---|---|
| Valid within source | 0722 openings ÷ 0722 active; 0722 net formation ÷ 0722 active; 1097 sector share; 1097 size-band share |
| Valid side-by-side with explicit labels | ISED 2023 context next to December 2025 scale; 0722 active enterprises next to 1097 locations for one CMA |
| Contextual overlay only | ISED E estimates, with a caution flag and a sensitivity check |
| Requires Phase E crosswalk | Joining shared CMA codes across 2016 and 2021 vintages; attaching ISED attributes to coded CMAs |
| **Prohibited or misleading** | See the list below |

The prohibited uses:
- 0722 flows ÷ 1097 locations;
- ISED percentages treated as counts;
- ISED weighted counts treated as register counts;
- multiplying one-way ISED percentages into CMA × industry × size cells;
- turning public aggregates into leads;
- ranking F estimates;
- comparing ISED segments without their SE/CV;
- summing overlapping aggregates;
- name-only geography joins;
- summing 0722 industries or CMAs;
- provincial totals built from 1097 CSDs;
- zero-filling unavailable cells;
- using 1097 as a time series;
- owner-demographic targeting.

### Phase E crosswalk requirements

**DECISION** (`phase_e_crosswalk_requirements.csv`): **8** Phase E crosswalk requirements. None has been built.

| ID | Mapping | Type | Unresolved |
|---|---|---|---|
| E1 | 0722 CMA (SGC 2016) → 1097 CMA (SGC 2021) | one-to-one by code, qualified | Boundary changes; 6 CMAs exist only in 1097 |
| E2 | ISED CMA labels → SGC 2021 CMA codes | many-to-one, explicit list | Sudbury; the Quebec collision; ISED boundaries undocumented |
| E3 | 0722 industry members → NAICS 2022 | many-to-one and one-to-many; tourism non-reconcilable | Scope of [11]; whether T004 includes Unclassified; 52+55; partial 61/62 |
| E4 | ISED industry groups → NAICS 2022 sector sets | many-to-one | Combined groups; exclusions inside groups; special aggregates |
| E5 | 1097 size bands → 0722/ISED bands | many-to-one (numeric), labelled location-based | Unit and methodology differ |
| E6 | Approximate the ISED population on 1097 | partial filter | Revenue and organizational form cannot be observed |
| E7 | Unclassified and overlapping aggregates | treatment rule (non-reconcilable) | Cannot be allocated to sectors |
| E8 | ISED regions and provinces → province codes | one-to-one and many-to-one | Atlantic grouped; BC combined with territories |

### Unresolved compatibility issues

- **Non-reconcilable** (8 items in `dimension_alignment_matrix.csv`):
  - location vs. enterprise units;
  - the three reference periods;
  - survey prevalence vs. counts;
  - 1097's lack of provincial totals;
  - CSD detail, which only 1097 has;
  - tourism and special aggregates.
- **Open questions carried from Phases A–C:**
  - the 2016 → 2021 boundary effect;
  - 0722's NAICS vintage and the scope of [11];
  - the ISED `..` meaning;
  - whether ISED's supplementary populations are included in its total.

### Phase D verdict

**The three sources support a coherent opportunity framework only as separate analytical layers** (**DECISION**, supported by **9** of 9 evidence checks):
- a **momentum layer** from 0722;
- a **scale and composition layer** from 1097;
- a **financing-context layer** from ISED 2023.

The layers may appear together only side by side with explicit unit and period labels. They meet only through the Phase E crosswalks above, and never through shared denominators, name joins or cell-level combinations.

### Validation workspace (Phase D)

| Item | Purpose |
|---|---|
| `src/validation/06_compare_source_grains.py` | Phase D comparison. Checks upstream gates, checksums and cache lineage; any failed gate exits with status 1 |
| `data/validation/source_comparison/*` | 10 compact outputs: grain, scope, alignment, measure-role, use-rule, join, temporal and Phase E requirement matrices; layer evidence; summary |

```bash
C:/Users/abhij/anaconda3/python.exe src/validation/06_compare_source_grains.py
```

---

## Crosswalks (Phase E)

The crosswalks are durable reference data in `data/reference/crosswalks/`. Their validation evidence is in `data/validation/crosswalks/`. Both are written by `src/validation/07_build_and_validate_crosswalks.py`, which first rechecks the Phase A–D gates, every raw checksum and both cache lineages.

Every mapping row carries an evidence source and a direct URL. The sources are **11** official references listed in `crosswalk_sources.csv`, and every column is described in `crosswalk_schema.csv`.

The crosswalks **align classification dimensions only.** They do not merge statistical units, periods or estimate types. No fuzzy or similarity matching is used anywhere; every non-identical label is an explicit, reviewed mapping.

### E1: SGC 2016 CMA → SGC 2021 CMA (`cma_sgc2016_to_sgc2021.csv`)

- **FACT: DATA.** **35** shared CMA codes mapped as exact code with boundary caveat, **6** target-only. The target-only CMAs are Fredericton, Drummondville, Red Deer, Kamloops, Chilliwack and Nanaimo.
- Matching uses CMA codes taken from the DGUIDs; names are never used. Both DGUIDs are kept.
- **FACT: SOURCE.** The final 2021 dictionary (S01) confirms that these six were CAs in 2016, and that Belleville became Belleville – Quinte West.
- **FACT: SOURCE.** In the **preliminary** 2021 delineation (S02, 2019), **10** of the shared CMAs had CSDs added. The largest additions were Ottawa–Gatineau (6), Windsor (3) and Halifax (2).
- **DECISION.** No shared code is treated as exact.
  - Every row carries a boundary flag.
  - 2016 observations are **not** recalculated onto 2021 boundaries, because no component-level concordance was used.

### E2: ISED CMA labels → SGC 2021 codes (`ised_cma_to_sgc2021.csv`)

- **FACT: DATA.** **24** ISED labels: **19** exact, **5** explicit aliases.
  - The aliases are Montreal, Ottawa-Gatineau, St. Catharines-Niagara and Kitchener-Cambridge-Waterloo (accents and spacing), and **Sudbury → 580 Greater Sudbury**.
- "Quebec" is mapped only in its CMA-row context (CMA 421), never to the province.
- Every code exists in 1097, and each CMA's province agrees with 1097's CSD province codes.
- **Caveat on every row:** the ISED workbook does not state its geography vintage, so ISED survey boundaries are **not** claimed to equal SGC 2021.

### E3: 0722 industry members → NAICS 2022 (`statcan_0722_industry_to_naics2022.csv`)

**FACT: DATA.** All **32** members in **40** rows, one row per member–code relationship. There are **18** sector-level members in the additive sector rollup, and no NAICS code appears in it twice.
- **52+55** maps to both sectors (combined category).
- **61 and 62 are partial mappings:** 61 → 6114–6117 per Note 12, and 62 → 621, 623 and 624 per Note 13. The script checks both against the note text.
- **[11] is unresolved.** It maps to 113, 114 and 115 with low confidence, because its scope is undocumented and it was a ratio outlier in Phase B.
- **Subsectors 311 and 312** map exactly, but are never added to sectors because they sit inside 31–33.
- **The 2 business-sector aggregates** are kept as aggregates, not NAICS members.
- The **10** tourism members are **non-reconcilable** and excluded from every additive join.
- 0722's NAICS vintage is not stated, so even exact codes carry medium-high confidence.

### E4: ISED industry groups → NAICS 2022 (`ised_industry_to_naics2022.csv`)

**FACT: DATA.** The **10** groups cover **15** sectors. Those are exactly the NAICS 2022 sectors left after the survey's sector exclusions (22, 52, 55, 61, 91), and no sector appears in two groups.
- **Combined groups:** 11+21, and 51+53+56+62+71.
- **Partial sectors** keep their survey exclusions visible:
  - 53 excludes 5321 and 5324;
  - 62 excludes 6214, 6215, 6219, 6221, 6222, 6223 and 6242;
  - 81 excludes 8141 (814110).
- **The 8 special aggregates are non-reconcilable** and non-additive. Their definitions are in an ISED methodology report that was not obtained.
- **DECISION.** ISED group percentages are never split into sectors.

### E5: 1097 size bands → broader SME bands (`statcan_1097_size_to_sme_bands.csv`)

- **FACT: DATA.** All 8 bands map exactly once, with no gaps or overlaps. The Total is handled separately, not as a ninth band.
- **The rollup reconciles on the 1097 cache: **334,464** of 334,464 cells** (geography × NAICS cells with a published total) match exactly (`size_rollup_reconciliation.csv`).
- The 500+ band is outside the ISED scope.
- **DECISION.** The result is always labelled *location-based employment-size composition*. **It is not an enterprise count.**

### E6: approximation of the ISED scope (`ised_scope_approximation_rules.csv`)

**FACT: SOURCE / DATA.** **26** rules: **17** exact, 1 partial (Unclassified, which cannot be tested) and **8** unobservable. Precedence is deterministic.

- **The exact exclusions use the survey record's own codes**, which aren't the shorthand range. The survey lists 6214, 6215 and 6219 but **not 6216**.
- **The exclusions:**
  - 500+ employees;
  - sectors 22, 52, 55, 61 and 91;
  - 4-digit groups 5321, 5324, 6214, 6215, 6219, 6221, 6222, 6223 and 6242;
  - 814110, applied through group 8141, which contains only that industry (S07).
- **Unobservable in 1097:**
  - revenue of at least $30,000;
  - non-profit, joint-venture, government and subsidiary status;
  - financing and leasing companies beyond the NAICS exclusions;
  - the enterprise unit;
  - other eligibility conditions.
- **Diagnostic** (41 CMAs, 1–499 employees): the result is **855,856** approximate ISED-scope employer locations, which is **84.8%** of the 1–499-employee locations. Unclassified is carried separately, and the waterfall reconciles exactly (`scope_filter_validation.csv`).
- **DECISION.** This number is **never** ISED-eligible SMEs, and it is never compared with ISED's weighted population. The units, geography, period and filters all differ.

### E7: non-additive categories (`non_additive_category_rules.csv`)

**FACT: DATA.** **28** non-additive rules cover:
- 1097 Unclassified: carried separately and never allocated to sectors. Sector shares must state whether their denominator is the total or the classified sub-total;
- 0722's tourism aggregates and components, business-sector aggregates, and subsectors 311 and 312;
- ISED's 8 special aggregates;
- ISED's nested geography rows (Northern and Southern Ontario, and the Prairie provinces).

A check confirms that no additive row in E3 or E4 contains any of these categories.

### E8: ISED regions and provinces → province codes (`ised_region_province_crosswalk.csv`)

**FACT: DATA.** **10** ISED region and province labels:
- **5 individual provinces map one-to-one:** Quebec 24, Ontario 35, Manitoba 46, Saskatchewan 47, Alberta 48.
- **Atlantic** → 10, 11, 12, 13.
- **Prairies** → 46, 47, 48.
- **British Columbia and Territories** → 59, 60, 61, 62. This is ISED's grouping; the SGC treats BC and the Territories as separate regions.
- **Northern and Southern Ontario: **2** unresolved.** ISED doesn't document their components.

The official codes (S10) agree with 0722's province DGUIDs and 1097's CSD codes. **No regional estimate is replicated to, or split among, provinces.**

### Validation results

- **FACT: DATA.** **52** of 52 crosswalk checks pass (`geography_mapping_validation.csv`, `industry_mapping_validation.csv`, `crosswalk_behaviour_checks.csv`).
- Coverage and cardinality are in `crosswalk_coverage.csv` and `crosswalk_cardinality.csv`. Every one-to-many relationship is declared, and there are no accidental duplicates.
- Unmatched, unresolved, partial and non-reconcilable records stay visible in `unmatched_and_unresolved.csv`.

### Permitted joins and prohibited interpretations (DECISION)

**Permitted:**
- 0722 ↔ 1097 CMA by code (E1), with the boundary flag shown;
- ISED CMA ↔ SGC 2021 code (E2) for **contextual attributes** only;
- 0722 sector members ↔ 1097 sectors (E3) for side-by-side reporting, with 61/62 partial and [11] unresolved;
- ISED groups ↔ 1097 sector sets (E4);
- 1097 composition in the broad bands (E5).

**Prohibited:**
- treating any crosswalk as a unit or period conversion;
- dividing across sources;
- summing overlapping categories;
- allocating Unclassified;
- calling the E6 approximation an SME count;
- replicating regional estimates to provinces;
- treating boundary-qualified CMA matches as exact.

### Phase E verdict (DECISION)

| Use | Sufficient? |
|---|---|
| CMA-level side-by-side reporting | **Yes, qualified:** by code, with boundary flags; 6 CMAs have no momentum data |
| Industry-level side-by-side reporting | **Yes, qualified:** at sector level; 61/62 partial, [11] unresolved, tourism excluded |
| Size-band composition reporting | **Yes:** exact rollup, labelled location-based |
| Contextual use of ISED financing measures | **Yes:** attached to coded CMAs, groups and regions as segment attributes |
| Approximating the ISED population | **Partial only:** industry and size filters are exact; revenue, organizational form and unit are unobservable |
| Combining values into one analytical table | **No.** Units, periods and estimate types stay separate |
| Selecting an MVP grain in Phase F | **Yes, the inputs are ready.** The candidate grains (CMA × sector × size band, CMA × sector, or CMA only) can now be evaluated, with these caveats |

**Remaining decisions for Phase F:**
- choose the analytical grain;
- decide whether to use the qualified [11] and partial 61/62 sectors;
- decide how to present the 6 CMAs that lack momentum data;
- decide whether E-coded ISED estimates enter any view.

### Validation workspace (Phase E)

```bash
C:/Users/abhij/anaconda3/python.exe src/validation/07_build_and_validate_crosswalks.py
```

The Phase D gate that checks no crosswalk was built is now scoped to Phase D's own output folder, so Phase E's reference files don't trip it. Phase D still passes 21 of 21.

---

## MVP analytical scope (Phase F)

**Status: decided.** The MVP scope, reporting spine and layer grains are documented in [03_mvp_analytical_scope.md](03_mvp_analytical_scope.md). The evidence is in `data/validation/mvp_grain/`, produced by `src/validation/08_select_mvp_grain.py`. The decision is to use one tiered CMA reporting spine (41 CMAs; 35 in the primary decision set) with separate views for each source; no combined dataset.

---

## Analytical data layer (Phase G)

**Status: built and validated.** The 11 views specified in Phase F are in `data/processed/analytical_views/` and documented in [04_analytical_data_layer.md](04_analytical_data_layer.md). They are built by `src/analytics/09_build_analytical_views.py`, with validation in `data/validation/analytical_views/`. Each fact view comes from one source only; no scores, rankings or leads are produced.
