-- Grain: one row per in-scope ISED table x measure x one-way segment (1,497).
-- Scope: Tables 1, 3, 4 and 49 by national, size band, CMA (Tier 1), region and the 10 industry groups;
-- Table 5 (deal size) national and size band only. Special aggregates and Table 50 (a two-way cross) are out.
-- Adds the segment code, measure definitions and the quality-based use status; values are never imputed.
with cells as (
    select * from {{ ref('stg_ised__financing_context') }}
),
scope as (
    select table_no, dimension_class, segment_dimension from {{ ref('ised_segment_scope') }}
),
aggregates as (
    select published_label from {{ ref('ised_industry_aggregate_codes') }}
)
select
    c.*,
    scope.segment_dimension,
    lk.segment_key,
    lk.geography_caveat,
    defs.block_title,
    defs.header_path,
    defs.unit,
    defs.universe_or_denominator,
    case when c.value_state = 'dotdot' then 'unavailable_dotdot'
         when c.value_state = 'blank' then 'unavailable_blank'
         when c.quality_code = 'F' then 'excluded_quality_F'
         when c.quality_code = 'E' then 'usable_with_caution_E'
         when c.quality_code is not null then 'usable_A_to_D'
         else 'published_without_quality_code' end               as mvp_use_status
from cells as c
join scope on scope.table_no = c.table_no and scope.dimension_class = c.dimension_class
left join {{ ref('int_financing__segment_lookup') }} as lk
  on lk.dimension_class = c.dimension_class and lk.stub_label = c.stub_label
left join {{ ref('stg_validated__ised_estimate_definitions') }} as defs on defs.measure_id = c.measure_id
where not (c.dimension_class = 'industry_naics_group' and c.stub_label in (select published_label from aggregates))
