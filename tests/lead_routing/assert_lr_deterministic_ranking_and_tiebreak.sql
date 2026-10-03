{{ config(tags=['lead_routing']) }}
-- Required tests: deterministic ranking and tie-breaking. Ranks form a 1..n permutation per decision, the ordering
-- agrees with the declared tie-break sequence, and the recorded separating tie-break is correct.
select 'rank is not a 1..n permutation' as issue, cast(decision_id as varchar) as id
from (select decision_id, count(*) n, min(candidate_rank) lo, max(candidate_rank) hi,
             count(distinct candidate_rank) d from {{ ref('int_lead_routing__candidate_ranking') }} group by 1)
where lo <> 1 or hi <> n or d <> n
union all
select 'ordering contradicts the declared tie-break sequence', a.decision_id || '|' || a.rm_id || '>' || b.rm_id
from {{ ref('int_lead_routing__candidate_ranking') }} a
join {{ ref('int_lead_routing__candidate_ranking') }} b
  on b.decision_id = a.decision_id and b.candidate_rank = a.candidate_rank + 1
where not (a.preference_score > b.preference_score
        or (a.preference_score = b.preference_score and a.utilization_ratio_at_decision < b.utilization_ratio_at_decision)
        or (a.preference_score = b.preference_score and a.utilization_ratio_at_decision = b.utilization_ratio_at_decision
            and a.remaining_capacity_points > b.remaining_capacity_points)
        or (a.preference_score = b.preference_score and a.utilization_ratio_at_decision = b.utilization_ratio_at_decision
            and a.remaining_capacity_points = b.remaining_capacity_points and a.rm_id < b.rm_id))
union all
select 'recorded separating tie-break is wrong', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where r.recommended_rm_id is not null
  and ((r.runner_up_preference_score is null and r.separating_tiebreak_id <> 'RTB-01')
    or (r.runner_up_preference_score is not null
        and r.recommended_preference_score > r.runner_up_preference_score and r.separating_tiebreak_id <> 'RTB-01')
    or (r.runner_up_preference_score is not null
        and r.recommended_preference_score = r.runner_up_preference_score and r.separating_tiebreak_id = 'RTB-01'))
union all
select 'preference score does not equal its published contributions', c.decision_id || '|' || c.rm_id
from {{ ref('mart_routing_candidate') }} c
where c.is_eligible and abs(c.preference_score - (c.pref_contribution_rpf_01 + c.pref_contribution_rpf_02
      + c.pref_contribution_rpf_03 + c.pref_contribution_rpf_04 + c.pref_contribution_rpf_05
      + c.pref_contribution_rpf_06 + c.pref_contribution_rpf_07)) > 1e-6
