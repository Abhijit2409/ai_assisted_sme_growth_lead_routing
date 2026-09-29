{{ config(tags=['opportunity']) }}
-- Monotonicity: raising any positively directed normalized component (holding all others constant) and recomputing the
-- pillar and final score through the published formula never lowers the score.
with f as (select * from {{ ref('mart_cma_opportunity_framework') }}),
bumped as (
    select cma_code_sgc2021, scenario_id, opportunity_score, component,
           weight_scale * (case when component = 'c01' then least(c01_normalized + 10, 100) else c01_normalized end)
         + weight_momentum * (within_momentum_weight_c05 * (case when component = 'c05' then least(c05_normalized + 10, 100) else c05_normalized end)
                              + within_momentum_weight_c09 * (case when component = 'c09' then least(c09_normalized + 10, 100) else c09_normalized end))
         + weight_dynamism * (case when component = 'c03' then least(c03_normalized + 10, 100) else c03_normalized end)
         + weight_commercial_fit * (case when component = 'c15' then least(c15_normalized + 10, 100) else c15_normalized end)
         + weight_stability * (case when component = 'c10' then least(c10_stability_normalized + 10, 100) else c10_stability_normalized end)
           as bumped_score
    from f cross join (select unnest(['c01', 'c03', 'c05', 'c09', 'c15', 'c10']) as component)
)
select * from bumped where bumped_score < opportunity_score - 1e-9
