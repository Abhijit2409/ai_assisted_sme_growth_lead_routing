-- Reviewed crosswalk E4: one row per ISED industry group or special aggregate x NAICS 2022 sector.
select * from {{ ref('ised_industry_to_naics2022') }}
