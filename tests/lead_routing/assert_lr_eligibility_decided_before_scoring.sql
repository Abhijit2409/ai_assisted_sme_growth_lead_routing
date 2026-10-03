{{ config(tags=['lead_routing']) }}
-- Required test: eligibility before scoring. A score exists only where priority eligibility is score_eligible, and
-- every score_eligible decision that was not suppressed by an override carries a score, a band and ten components.
select p.decision_id, p.priority_eligibility, p.recommended_priority_score, p.override_rule_id, p.component_count
from {{ ref('mart_lead_priority') }} p
where (p.recommended_priority_score is not null and p.priority_eligibility <> 'score_eligible')
   or (p.priority_eligibility = 'score_eligible' and p.override_action is distinct from 'suppress_score'
       and (p.recommended_priority_score is null or p.recommended_priority_band is null or p.component_count <> 10))
   or (p.priority_eligibility <> 'score_eligible' and p.component_count is not null)
