-- Grain: one row per SGC province or territory code (13).
-- The ISED region that gives a province its regional context: the province itself where ISED
-- publishes it individually, otherwise the grouped region that contains it. Unresolved
-- sub-provincial regions are never used.
with e8 as (
    select * from {{ ref('stg_reference__ised_region_province_crosswalk') }}
    where mapping_status <> 'unresolved'
),
ranked as (
    select target_code as province_code, source_label,
           row_number() over (partition by target_code
                              order by (dimension_type = 'individual province') desc, source_label) as preference
    from e8
)
select ranked.province_code, keys.region_key as ised_region_key
from ranked
join {{ ref('int_financing__ised_region_keys') }} as keys on keys.ised_region_label = ranked.source_label
where ranked.preference = 1
