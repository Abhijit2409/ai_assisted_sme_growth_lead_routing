-- Grain: one row per full-framework CMA (35) x component. Orients every component so that higher = stronger external
-- opportunity evidence (lower_is_stronger components are negated), then normalizes it four ways:
--   percentile rank with average ties (primary; declared before scoring), min-max, min-max after capping at the
--   Tukey fences (raw value preserved, caps recorded) and a robust z-score (median, IQR / 1.349) clipped to +/-3,
--   each on 0-100. The alternatives exist only for sensitivity analysis.
with eligible as (
    select cma_code_sgc2021 from {{ ref('dim_cma') }} where momentum_comparison_eligible
),
directed as (
    select t.*, r.direction, r.pillar, r.within_pillar_weight, r.framework_role,
           case when r.direction = 'lower_is_stronger' then -t.transformed_value else t.transformed_value end as directed_value
    from {{ ref('int_opportunity__transformed_indicators') }} as t
    join {{ ref('opportunity_indicator_registry') }} as r using (component_id)
    where t.cma_code_sgc2021 in (select cma_code_sgc2021 from eligible)
),
stats as (
    select component_id, min(directed_value) as min_value, max(directed_value) as max_value,
           quantile_cont(directed_value, 0.25) as q1, quantile_cont(directed_value, 0.5) as median_value,
           quantile_cont(directed_value, 0.75) as q3, count(*) as population_size
    from directed group by component_id
),
capped as (
    select d.*, s.min_value, s.max_value, s.median_value, s.q1, s.q3, s.population_size,
           s.q1 - 1.5 * (s.q3 - s.q1) as lower_cap, s.q3 + 1.5 * (s.q3 - s.q1) as upper_cap,
           least(greatest(d.directed_value, s.q1 - 1.5 * (s.q3 - s.q1)), s.q3 + 1.5 * (s.q3 - s.q1)) as winsorized_directed_value
    from directed as d join stats as s using (component_id)
),
capped_range as (
    select component_id, min(winsorized_directed_value) as w_min, max(winsorized_directed_value) as w_max from capped group by component_id
)
select
    c.cma_code_sgc2021, c.component_id, c.indicator_id, c.pillar, c.within_pillar_weight, c.direction, c.framework_role,
    c.raw_value, c.transformed_value, c.transformation, c.directed_value,
    {{ average_rank_percentile('c.directed_value', 'c.component_id') }}                                        as normalized_percentile,
    -- min-max values are clamped to [0, 100] only to remove floating-point overshoot (for example 100.00000000000001)
    least(greatest(100.0 * (c.directed_value - c.min_value) / nullif(c.max_value - c.min_value, 0), 0), 100)   as normalized_min_max,
    least(greatest(100.0 * (c.winsorized_directed_value - w.w_min) / nullif(w.w_max - w.w_min, 0), 0), 100)     as normalized_min_max_winsorized,
    100.0 * (least(greatest((c.directed_value - c.median_value) / nullif((c.q3 - c.q1) / 1.349, 0), -3), 3) + 3) / 6 as normalized_robust_z,
    c.winsorized_directed_value <> c.directed_value                                                             as winsorization_applied,
    c.lower_cap, c.upper_cap,
    count(*) over (partition by c.component_id, c.directed_value)                                              as tie_count,
    c.population_size,
    c.usable_observations, c.required_observations, c.e_flagged_observations, c.input_status,
    c.source_model, c.source_columns, c.period, c.statistical_unit, c.scale_adjustment_slope, c.scale_adjustment_intercept
from capped as c
join capped_range as w using (component_id)
