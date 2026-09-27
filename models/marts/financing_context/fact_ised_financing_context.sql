-- ISED survey estimates (ISED only): one row per table x measure x one-way segment. Contextual evidence only.
with estimates as (
    select * from {{ ref('int_financing__estimates_in_scope') }}
),
nested_regions as (
    select industry_code as region_key, rule_id from {{ ref('non_additive_rule_links') }} where industry_system = 'ISED_REGION'
)
select
    e.source_id,
    e.survey_year,
    'ISED SFGSME 2023 survey (calendar 2023)'                                      as period_label,
    e.table_no                                                                    as ised_table,
    e.measure_id,
    e.block_title || ' | ' || e.header_path                                       as measure_label,
    e.estimate_type,
    e.unit,
    e.universe_or_denominator,
    case e.estimate_type
        when 'percentage_of_universe' then 'survey estimate (% of SMEs in segment)'
        when 'weighted_count' then 'survey estimate (weighted SME enterprises)'
        when 'unweighted_respondent_count' then 'survey respondents (unweighted count)'
        when 'percentage_share_of_total' then 'survey estimate (% share of weighted SMEs)'
        when 'weighted_total_monetary' then 'survey estimate (dollars, weighted total)'
        when 'average_monetary' then 'survey estimate (dollars, average)'
        when 'ratio_of_totals' then 'survey estimate (% ratio of weighted totals)' end as statistical_unit,
    case when e.estimate_type = 'unweighted_respondent_count' then 'survey sample size (respondents)'
         else 'filtered SME survey measure' end                                   as scope_class,
    case
        when e.measure_id = 'ISED_T01_M001' then 'I01'
        when e.measure_id = 'ISED_T03_M001' then 'I02'
        when e.measure_id in ('ISED_T03_M002', 'ISED_T03_M003', 'ISED_T03_M004', 'ISED_T03_M005', 'ISED_T03_M006', 'ISED_T03_M007') then 'I03'
        when e.measure_id = 'ISED_T49_M002' then 'I06'
        when e.table_no = '4' then 'I04'
        when e.table_no = '5' then 'I05'
    end                                                                           as metric_id,
    e.segment_dimension,
    e.segment_key,
    e.stub_label                                                                  as segment_label_published,
    e.stub_level                                                                  as segment_level,
    e.segment_dimension = 'region' and n.region_key is not null                   as nested_region_flag,
    case when e.segment_dimension = 'region' then n.rule_id end                   as non_additive_rule_id,
    e.geography_caveat,
    e.value_published,
    e.value_state,
    e.quality_code,
    e.error_value,
    e.error_type                                                                  as se_or_cv,
    coalesce(e.quality_code = 'E', false)                                         as caution_flag,
    coalesce(e.quality_code = 'F', false)                                         as excluded_quality_f,
    e.value_state = 'dotdot'                                                      as dotdot_flag,
    e.mvp_use_status,
    e.stub_row                                                                    as source_stub_row,
    case when e.mvp_use_status in ('usable_A_to_D', 'usable_with_caution_E', 'published_without_quality_code')
         then e.value_published end                                              as value_usable
from estimates as e
left join nested_regions as n on e.segment_dimension = 'region' and n.region_key = e.segment_key
order by ised_table, measure_id, segment_dimension, segment_key
