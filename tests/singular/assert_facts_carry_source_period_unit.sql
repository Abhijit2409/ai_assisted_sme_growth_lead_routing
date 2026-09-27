-- Every fact row carries its own source identity, period and statistical unit, and only its own source.
{%- set facts = [
    ('fact_cma_momentum_monthly', 'ref_month', var('statcan_0722_source_id')),
    ('fact_cma_market_scale_snapshot', 'snapshot_period', var('statcan_1097_source_id')),
    ('fact_cma_sector_composition_snapshot', 'snapshot_period', var('statcan_1097_source_id')),
    ('fact_cma_size_composition_snapshot', 'snapshot_period', var('statcan_1097_source_id')),
    ('fact_ised_financing_context', 'survey_year', var('ised_source_id')),
] %}
{%- for model, period, source_id in facts %}
select '{{ model }}' as model, count(*) as rows_failing
from {{ ref(model) }}
where source_id is null or source_id <> '{{ source_id }}' or {{ period }} is null
   or statistical_unit is null or statistical_unit = '' or scope_class is null
having count(*) > 0
{%- if not loop.last %}
union all
{%- endif %}
{%- endfor %}
