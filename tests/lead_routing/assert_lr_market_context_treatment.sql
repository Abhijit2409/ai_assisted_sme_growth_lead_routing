{{ config(tags=['lead_routing']) }}
-- Required tests: Tier 3 treatment and missing-market-context treatment. Neither is scored as zero opportunity, both
-- take the declared neutral value, and external market context never exceeds its declared share of the score.
with cap as (select cast(value as double) c from {{ ref('routing_evaluation_thresholds') }} where parameter = 'market_context_max_share_of_score')
select 'market context neutral value not the declared one' as issue, c.decision_id as id
from {{ ref('int_lead_routing__priority_components') }} c
where c.component_id = 'BV05_MARKET' and c.used_neutral_default and c.normalized_value <> c.declared_neutral_value
union all
select 'tier 3 or missing context not treated as neutral', p.decision_id
from {{ ref('mart_lead_priority') }} p
where p.market_context_treatment in ('neutral_tier3_scale_only', 'neutral_missing_context')
  and p.recommended_priority_score is not null
  and (not p.market_used_neutral_default or p.market_normalized_value is distinct from 50.0)
union all
select 'tier 3 lead treated as zero opportunity', p.decision_id
from {{ ref('mart_lead_priority') }} p
where p.evidence_tier = 3 and p.recommended_priority_score is not null
  and (p.market_normalized_value is null or p.market_normalized_value <= 0)
union all
select 'market context exceeds its declared cap', cast(max_share as varchar)
from (select max(effective_weight) as max_share
      from {{ ref('int_lead_routing__priority_components') }} where component_id = 'BV05_MARKET')
cross join cap where max_share > cap.c + 1e-9
