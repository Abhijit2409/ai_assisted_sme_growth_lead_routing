-- Grain: one row per core component left out x full-framework CMA (balanced scenario, primary normalization).
-- The left-out component is dropped from its pillar (remaining components re-weighted within the pillar); if the pillar
-- becomes empty, its weight is redistributed proportionally to the remaining pillars.
with thresholds as (
    select
        max(case when parameter = 'primary_normalization' then value end)                        as primary_normalization,
        max(case when parameter = 'top_quartile_percentile' then cast(value as double) end)      as top_quartile_percentile,
        max(case when parameter = 'bottom_quartile_percentile' then cast(value as double) end)   as bottom_quartile_percentile,
        max(case when parameter = 'lead_pillar_threshold' then cast(value as double) end)        as lead_pillar_threshold,
        max(case when parameter = 'other_pillar_ceiling' then cast(value as double) end)         as other_pillar_ceiling,
        max(case when parameter = 'driver_gap' then cast(value as double) end)                   as driver_gap,
        max(case when parameter = 'rank_sensitivity_threshold' then cast(value as double) end)   as rank_sensitivity_threshold,
        max(case when parameter = 'momentum_clarity_multiple' then cast(value as double) end)    as momentum_clarity_multiple
    from {{ ref('opportunity_decision_thresholds') }}
),
core as (
    select component_id from {{ ref('opportunity_indicator_registry') }} where framework_role = 'core score'
),
normalized as (
    select cma_code_sgc2021, component_id, pillar, within_pillar_weight, normalized_percentile as normalized_value
    from {{ ref('int_opportunity__normalized_indicators') }}
    where component_id in (select component_id from core)
),
pillars as (
    select c.component_id as left_out_component, n.cma_code_sgc2021, n.pillar,
           sum(n.within_pillar_weight * n.normalized_value order by n.component_id) / sum(n.within_pillar_weight order by n.component_id) as pillar_score
    from core as c
    join normalized as n on n.component_id <> c.component_id
    group by all
),
weights as (
    select pillar, pillar_weight from {{ ref('opportunity_weight_scenarios') }} where scenario_id = 'balanced' and pillar_weight > 0
),
scored as (
    select p.left_out_component, p.cma_code_sgc2021,
           round(sum(w.pillar_weight * p.pillar_score order by p.pillar) / sum(w.pillar_weight order by p.pillar), 9) as opportunity_score   -- fixed order; rounded to 1e-9
    from pillars as p join weights as w using (pillar)
    group by all
)
select *, rank() over (partition by left_out_component order by round(opportunity_score, 9) desc) as score_rank
from scored
