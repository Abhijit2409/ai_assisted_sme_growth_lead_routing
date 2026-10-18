# Source attribution

This project is a **value-added product** that adapts public information published by the Government of Canada. It
is not an official product, and nothing in it is endorsed by the originating agencies.

Per-file provenance — SHA-256, byte size, source title, source URL and reference period for every raw input — is
recorded in [`data/source_manifest.csv`](data/source_manifest.csv).

## Statistics Canada

Used under the [Statistics Canada Open Licence](https://www.statcan.gc.ca/en/reference/licence).

### Table 33-10-0722-01

> Adapted from Statistics Canada, *Experimental estimates for business openings and closures by employment size for
> Canada, provinces and territories, census metropolitan areas, seasonally adjusted* (Table 33-10-0722-01),
> reference period 2015-01-01 to 2026-05-01. This does not constitute an endorsement by Statistics Canada of this
> product.

Source table: <https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3310072201>

Statistical unit: **enterprises**. Used for business opening, closure and net-formation momentum.

### Table 33-10-1097-01

> Adapted from Statistics Canada, *Canadian Business Counts, with employees, census metropolitan areas and census
> subdivisions, December 2025* (Table 33-10-1097-01), reference period 2025-07-01. This does not constitute an
> endorsement by Statistics Canada of this product.

Source table: <https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3310109701>

Statistical unit: **locations**, not businesses. Used for market scale, sector composition and size composition.

## Innovation, Science and Economic Development Canada (ISED)

> Adapted from *Survey on Financing and Growth of Small and Medium Enterprises, 2023 — Data tables*, produced by
> Statistics Canada in collaboration with the ISED Small Business Branch; data released 2025-02-20. This does not
> constitute an endorsement by Statistics Canada of this product.

Source dataset:
<https://open.canada.ca/data/en/dataset/c3c9f361-c7d3-4c87-8be0-64c2b62521ee>

Statistical unit: **survey estimates**. Survey reference year 2023; collection 2024-02-22 to 2024-06-24.

Used as **contextual evidence only**. ISED estimates are never used as market-size inputs, never used as a
denominator for the Statistics Canada counts, and never attached to a census metropolitan area — the survey does
not state its geography vintage.

## Open Government Licence

> Contains information licensed under the
> [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).

## Synthetic data

Everything under `data/synthetic/`, and every operational figure derived from it (leads, relationship managers,
assignments, activities, SLA events, capacity snapshots, routing decisions and conversion outcomes), is **entirely
synthetic**. It was generated deterministically for this project from a declared seed and a reviewed design, and
carries no relationship to any real business, person or financial institution.

Synthetic records deliberately use reserved placeholder formats: `.example` email domains and the fictional
`555-01xx` telephone range. A validator asserts that the generated data contains no real bank names and no
social-insurance, business-number or account-number patterns.

## Repository code and documentation

Released under the [MIT Licence](LICENSE). The MIT Licence applies to this project's own work only, not to the
public source data described above.
