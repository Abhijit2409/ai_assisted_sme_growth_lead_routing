-- Deterministic candidate ranking.
-- Grain: one row per decision_id x rm_id among eligible candidates (primary key); candidate_rank is unique within a decision.
-- Tie-break order comes from seeds/lead_routing/routing_tiebreak_rules.csv:
--   RTB-01 preference_score desc, RTB-02 utilization_ratio_at_decision asc, RTB-03 remaining_capacity_points desc,
--   RTB-04 rm_id asc (stable fictional identifier, applied only after every business preference).
-- No randomness is used. The tie-break level that actually separated the winner from the runner-up is published.
with ranked as (
    select
        p.*,
        row_number() over (
            partition by p.decision_id
            order by p.preference_score desc, p.utilization_ratio_at_decision asc,
                     p.remaining_capacity_points desc, p.rm_id asc)                       as candidate_rank,
        count(*) filter (where true) over (partition by p.decision_id, p.preference_score) as tied_on_preference_score
    from {{ ref('int_lead_routing__candidate_preference') }} p
),

runner_up as (
    select
        r.decision_id,
        max(case when r.candidate_rank = 1 then r.preference_score end)                 as top_preference_score,
        max(case when r.candidate_rank = 2 then r.preference_score end)                 as second_preference_score,
        max(case when r.candidate_rank = 1 then r.utilization_ratio_at_decision end)    as top_utilization,
        max(case when r.candidate_rank = 2 then r.utilization_ratio_at_decision end)    as second_utilization,
        max(case when r.candidate_rank = 1 then r.remaining_capacity_points end)        as top_remaining,
        max(case when r.candidate_rank = 2 then r.remaining_capacity_points end)        as second_remaining
    from ranked r
    group by 1
)

select
    r.*,
    (r.candidate_rank = 1)                                       as is_recommended_candidate,
    case
        when u.second_preference_score is null                                             then 'RTB-01'
        when u.top_preference_score is distinct from u.second_preference_score             then 'RTB-01'
        when u.top_utilization       is distinct from u.second_utilization                 then 'RTB-02'
        when u.top_remaining         is distinct from u.second_remaining                   then 'RTB-03'
        else 'RTB-04'
    end                                                          as separating_tiebreak_id,
    (case
        when u.second_preference_score is null                                             then false
        when u.top_preference_score is distinct from u.second_preference_score             then false
        else true
     end)                                                        as tiebreak_beyond_preference_applied,
    u.second_preference_score,
    '{{ var("lead_routing_rule_version") }}'                     as rule_version
from ranked r
join runner_up u on u.decision_id = r.decision_id
