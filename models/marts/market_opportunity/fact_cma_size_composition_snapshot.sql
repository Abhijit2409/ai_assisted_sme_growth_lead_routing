-- SME employer locations by broad size band (StatCan 1097 only): one row per CMA x band (4 SME bands + 500+).
-- Location-based employment-size composition; not enterprise size.
with cells as (
    select * from {{ ref('int_market__cma_location_cells') }} where naics_level = 'table_total' and size_member_id between 2 and 9
),
aggregated as (
    select cma_code_sgc2021, size_band_key, cast(sum(coalesce(value_num, 0)) as bigint) as locations,
           cast(sum(case when omitted then 0 else 1 end) as bigint) as n_published_cells,
           cast(sum(case when omitted then 1 else 0 end) as bigint) as n_zero_by_reconciliation_cells,
           bool_or(omitted) as omitted_zero_flag,
           string_agg(size_member_id::varchar, ';' order by size_member_id) as source_size_member_ids
    from cells
    group by all
),
with_total as (
    select *, sum(case when size_band_key <> '500-plus' then locations end) over (partition by cma_code_sgc2021) as sme_total
    from aggregated
)
select
    '{{ var("statcan_1097_source_id") }}'  as source_id,
    'location'                             as statistical_unit,
    '{{ var("snapshot_period_1097") }}'    as snapshot_period,
    '{{ var("source_ref_date_1097") }}'    as source_ref_date,
    cma_code_sgc2021,
    size_band_key,
    size_band_key <> '500-plus'                                                   as in_sme_scope,
    case when size_band_key = '500-plus' then 'outside SME scope (500+)' else 'SME-location measure' end as scope_class,
    locations,
    case when size_band_key <> '500-plus' then 'S04' end                          as share_metric_id,
    case when size_band_key <> '500-plus' then locations::double / sme_total end  as share_of_sme_locations,
    cast(sme_total as bigint)                                                     as share_denominator,
    true                                                                          as location_basis_flag,
    'location-based employment-size composition; not enterprise size'            as unit_note,
    n_published_cells,
    n_zero_by_reconciliation_cells,
    omitted_zero_flag,
    source_size_member_ids,
    'Total, all industries'                                                       as industry_scope
from with_total
order by cma_code_sgc2021, size_band_key, snapshot_period
