{{ config(tags=['opportunity']) }}
-- Every classification follows its declared, ordered rule (thresholds from opportunity_decision_thresholds).
with t as (
    select max(case when parameter = 'top_quartile_percentile' then cast(value as double) end) as top_q,
           max(case when parameter = 'bottom_quartile_percentile' then cast(value as double) end) as bottom_q,
           max(case when parameter = 'lead_pillar_threshold' then cast(value as double) end) as lead,
           max(case when parameter = 'other_pillar_ceiling' then cast(value as double) end) as ceiling
    from {{ ref('opportunity_decision_thresholds') }}
)
select f.cma_code_sgc2021, f.scenario_id, f.opportunity_classification
from {{ ref('mart_cma_opportunity_framework') }} as f cross join t
where f.opportunity_classification <> case
    when f.score_percentile >= t.top_q then 'Stronger external opportunity signal'
    when f.pillar_scale >= t.lead and f.pillar_momentum < t.ceiling then 'Scale-led external opportunity signal'
    when f.pillar_momentum >= t.lead and f.pillar_scale < t.ceiling then 'Momentum-led external opportunity signal'
    when f.score_percentile >= t.bottom_q then 'Moderate external opportunity signal'
    else 'Weaker current external signal (monitor)' end
