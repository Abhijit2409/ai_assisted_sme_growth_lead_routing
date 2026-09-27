-- One row per broad size band (1-4, 5-19, 20-99, 100-499, 500+) plus the total. Classification only:
-- the same keys describe enterprises (0722, ISED) and locations (1097), so the unit is stated on each fact row.
with e5 as (
    select * from {{ ref('stg_reference__statcan_1097_size_to_sme_bands') }}
),
bands as (
    select target_code as size_band_key,
           min(target_label) as size_band_label, min(target_lower) as lower_employees, min(target_upper) as upper_employees,
           string_agg(source_code, ';' order by cast(source_code as integer)) as source_1097_size_member_ids
    from e5
    group by target_code
)
select
    b.size_band_key,
    b.size_band_label,
    b.lower_employees,
    b.upper_employees,
    cast(o.sort_order as bigint)                       as sort_order,
    b.size_band_key = 'TOTAL'                          as is_total,
    o.in_sme_scope = 'True'                            as in_sme_scope,
    cast(s.size_member_id as bigint)                   as source_0722_size_member_id,
    b.source_1097_size_member_ids,
    i.published_label                                  as ised_segment_label,
    'classification only; the statistical unit (enterprise, location or survey estimate) is carried on each fact and bridge row' as unit_basis
from bands as b
join {{ ref('broad_size_band_order') }} as o on o.size_band_key = b.size_band_key
left join {{ ref('statcan_0722_size_members') }} as s on s.size_band_key = b.size_band_key
left join {{ ref('ised_size_segments') }} as i on i.size_band_key = b.size_band_key
order by sort_order
