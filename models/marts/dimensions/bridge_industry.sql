-- One row per source member x NAICS 2022 target (40 table-0722 rows from E3 + 23 ISED rows from E4).
-- Values are never allocated across one-to-many rows (value_allocation_allowed = false).
with e3 as (
    select
        'STATCAN_0722'                   as source_system,
        source_member_id                 as source_code,
        source_code                      as source_published_code,
        source_label,
        'NAICS2022'                      as target_system,
        target_code,
        target_label,
        mapping_type,
        mapping_status,
        sector_rollup_flag = 'True'      as additive_flag,
        source_class                     as inclusion_type,
        cast(null as varchar)            as excluded_codes_within,
        confidence,
        crosswalk_id
    from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
),
e4 as (
    select
        'ISED_SFGSME_2023'               as source_system,
        coalesce(e4.source_code, agg.industry_code) as source_code,
        cast(null as varchar)            as source_published_code,
        e4.source_label,
        'NAICS2022'                      as target_system,
        e4.target_code,
        e4.target_label,
        e4.mapping_type,
        e4.mapping_status,
        e4.additive_flag = 'True'        as additive_flag,
        e4.inclusion_type,
        e4.excluded_codes_within,
        e4.confidence,
        e4.crosswalk_id
    from {{ ref('stg_reference__ised_industry_to_naics2022') }} as e4
    left join {{ ref('ised_industry_aggregate_codes') }} as agg on agg.published_label = e4.source_label
)
select *, false as value_allocation_allowed
from (select * from e3 union all select * from e4)
order by source_system, source_code, target_code
