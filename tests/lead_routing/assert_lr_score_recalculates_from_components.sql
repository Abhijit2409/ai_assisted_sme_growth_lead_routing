{{ config(tags=['lead_routing']) }}
-- Required test: score recalculation. The published score equals the sum of its ten published contributions, each
-- contribution equals pillar weight x component weight x normalized value, and the declared weights sum to one.
with tol as (select cast(value as double) t from {{ ref('routing_evaluation_thresholds') }} where parameter = 'score_comparison_tolerance'),
rebuilt as (
    select decision_id, sum(contribution) s, count(*) n,
           sum(case when abs(contribution - pillar_weight * component_weight * normalized_value) > 1e-9 then 1 else 0 end) bad
    from {{ ref('int_lead_routing__priority_components') }} group by 1
),
declared as (
    select
        sum(case when level = 'pillar' then weight else 0 end) pillar_sum,
        sum(case when level = 'component' and pillar = 'business_value' then weight else 0 end) bv_sum,
        sum(case when level = 'component' and pillar = 'operational_urgency' then weight else 0 end) ou_sum
    from {{ ref('lead_priority_weight_config') }}
)
select 'score mismatch' as issue, r.decision_id as id from rebuilt r
join {{ ref('mart_lead_priority') }} p on p.decision_id = r.decision_id cross join tol
where abs(p.base_priority_score - r.s) > greatest(tol.t, 5e-7) or r.n <> 10 or r.bad > 0
union all
select 'declared weights do not sum to one', cast(pillar_sum as varchar) || '|' || cast(bv_sum as varchar) || '|' || cast(ou_sum as varchar)
from declared where abs(pillar_sum - 1.0) > 1e-9 or abs(bv_sum - 1.0) > 1e-9 or abs(ou_sum - 1.0) > 1e-9
