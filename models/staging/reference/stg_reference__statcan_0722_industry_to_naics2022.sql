-- Reviewed crosswalk E3: one row per 0722 industry member x NAICS 2022 code.
select * from {{ ref('statcan_0722_industry_to_naics2022') }}
