-- Grain: one row per ISED region or province label (10).
-- The region key is the set of SGC province codes it covers (for example '10;11;12;13'); the two
-- sub-provincial Ontario regions, whose components ISED does not document, take reviewed keys.
with e8 as (
    select * from {{ ref('stg_reference__ised_region_province_crosswalk') }}
),
subprovincial as (
    select published_label, region_key from {{ ref('ised_subprovincial_region_keys') }}
)
select
    e8.source_label                                         as ised_region_label,
    case when bool_and(e8.mapping_status = 'unresolved') then any_value(sub.region_key)
         else string_agg(e8.target_code, ';' order by e8.target_code) end as region_key,
    bool_and(e8.mapping_status = 'unresolved')              as is_unresolved_subprovincial
from e8
left join subprovincial as sub on sub.published_label = e8.source_label
group by e8.source_label
