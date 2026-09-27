-- Employer locations (StatCan 1097 only): one row per CMA x size scope (all sizes, 1-499, 500+).
with cells as (
    select * from {{ ref('int_market__cma_location_cells') }} where naics_level = 'table_total'
),
scoped as (
    select cma_code_sgc2021, 'all_sizes' as size_scope, size_member_id, value_num, omitted from cells where size_member_id = 1
    union all
    select cma_code_sgc2021, 'sme_1_499', size_member_id, value_num, omitted from cells where size_member_id between 2 and 8
    union all
    select cma_code_sgc2021, '500_plus', size_member_id, value_num, omitted from cells where size_member_id = 9
)
select
    '{{ var("statcan_1097_source_id") }}'  as source_id,
    'location'                             as statistical_unit,
    '{{ var("snapshot_period_1097") }}'    as snapshot_period,
    '{{ var("source_ref_date_1097") }}'    as source_ref_date,
    cma_code_sgc2021,
    size_scope,
    case size_scope when 'all_sizes' then 'S01' when 'sme_1_499' then 'S02' end as metric_id,
    case size_scope when 'all_sizes' then 'all-employer-business context' when 'sme_1_499' then 'SME-location measure'
         else 'outside SME scope (500+)' end                                     as scope_class,
    cast(sum(coalesce(value_num, 0)) as bigint)                                   as locations,
    count(*)                                                                      as n_component_cells,
    cast(sum(case when omitted then 0 else 1 end) as bigint)                      as n_published_cells,
    cast(sum(case when omitted then 1 else 0 end) as bigint)                      as n_zero_by_reconciliation_cells,
    bool_or(omitted)                                                              as omitted_zero_flag,
    string_agg(size_member_id::varchar, ';' order by size_member_id)              as source_size_member_ids,
    'Total, all industries'                                                       as industry_scope
from scoped
group by all
order by cma_code_sgc2021, size_scope, snapshot_period
