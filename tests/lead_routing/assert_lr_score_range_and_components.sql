{{ config(tags=['lead_routing']) }}
-- Required test: score range. Every published score is within 0-100, every component normalized value is within 0-100,
-- and no component uses a zero neutral default.
select 'score out of range' as issue, p.decision_id as id
from {{ ref('mart_lead_priority') }} p
where p.recommended_priority_score is not null and (p.recommended_priority_score < 0 or p.recommended_priority_score > 100)
union all
select 'component normalized out of range', c.decision_id || '|' || c.component_id
from {{ ref('int_lead_routing__priority_components') }} c
where c.normalized_value < 0 or c.normalized_value > 100
union all
select 'neutral default is zero', c.decision_id || '|' || c.component_id
from {{ ref('int_lead_routing__priority_components') }} c
where c.used_neutral_default and c.normalized_value <= 0
