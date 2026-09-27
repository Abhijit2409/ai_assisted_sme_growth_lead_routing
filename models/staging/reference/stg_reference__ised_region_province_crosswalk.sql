-- Reviewed crosswalk E8: one row per ISED region/province label x SGC province code.
select * from {{ ref('ised_region_province_crosswalk') }}
