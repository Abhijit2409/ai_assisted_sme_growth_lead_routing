-- SME employer locations by NAICS sector (StatCan 1097 only): one row per CMA x sector (19 business sectors,
-- public administration 91 and Unclassified shown separately). Shares use the classified 19-sector denominator.
with cells as (
    select * from {{ ref('int_market__cma_location_cells') }}
    where naics_level in ('sector', 'unclassified') and size_member_id between 1 and 8
),
aggregated as (
    select cma_code_sgc2021, industry_code as naics_sector,
           cast(sum(case when size_member_id > 1 then coalesce(value_num, 0) end) as bigint)  as sme_locations,
           cast(sum(case when size_member_id = 1 then coalesce(value_num, 0) end) as bigint)  as locations_all_sizes,
           cast(sum(case when size_member_id > 1 and not omitted then 1 else 0 end) as bigint) as n_published_cells,
           cast(sum(case when size_member_id > 1 and omitted then 1 else 0 end) as bigint)     as n_zero_by_reconciliation_cells,
           bool_or(size_member_id > 1 and omitted)                                            as omitted_zero_flag
    from cells
    group by all
),
with_denominators as (
    select *, naics_sector not in ('91', 'UNCLASSIFIED') as in_share_denominator,
           sum(case when naics_sector not in ('91', 'UNCLASSIFIED') then sme_locations end) over (partition by cma_code_sgc2021) as classified_business_sme,
           sum(sme_locations) over (partition by cma_code_sgc2021) as sme_total
    from aggregated
)
select
    '{{ var("statcan_1097_source_id") }}'  as source_id,
    'location'                             as statistical_unit,
    '{{ var("snapshot_period_1097") }}'    as snapshot_period,
    '{{ var("source_ref_date_1097") }}'    as source_ref_date,
    cma_code_sgc2021,
    naics_sector,
    case when naics_sector = 'UNCLASSIFIED' then 'unclassified_separate' when naics_sector = '91' then 'public_administration_separate'
         else 'business_sector' end                                               as sector_role,
    naics_sector = 'UNCLASSIFIED'                                                 as unclassified_separate,
    naics_sector = '91'                                                           as excluded_91_flag,
    'SME-location measure'                                                        as scope_class,
    '1-499'                                                                       as size_scope,
    sme_locations,
    locations_all_sizes,
    case when in_share_denominator then 'S03' when naics_sector = 'UNCLASSIFIED' then 'S05' end as share_metric_id,
    case when in_share_denominator then sme_locations::double / classified_business_sme
         when naics_sector = 'UNCLASSIFIED' then sme_locations::double / sme_total end as share,
    case when in_share_denominator then 'classified 1-499 locations in the 19 business sectors (excludes 91 and Unclassified)'
         when naics_sector = 'UNCLASSIFIED' then 'all 1-499 locations (Unclassified share of SME locations)' end as share_denominator_basis,
    cast(case when in_share_denominator then classified_business_sme when naics_sector = 'UNCLASSIFIED' then sme_total end as bigint) as share_denominator,
    n_published_cells,
    n_zero_by_reconciliation_cells,
    omitted_zero_flag
from with_denominators
order by cma_code_sgc2021, naics_sector, snapshot_period
