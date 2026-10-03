-- Controlled queue outcomes for every decision that cannot be recommended to an RM.
-- Grain: one row per decision_id that ends in a queue (primary key).
-- Two sources: a failed lead-level gate (REL-01 to REL-05), or no eligible candidate after the candidate-level rules.
-- The queue code for the second case is chosen from the rule that excluded the largest share of the candidate pool,
-- in the declared queue priority order, so the reason given to the queue owner is the one that must actually be fixed.
-- Every row carries the queue code, reason, failed rules, owner, SLA, next action and escalation path.
with population as (
    select * from {{ ref('int_lead_routing__routing_decision_population') }}
),

no_eligible_candidate as (
    select
        c.decision_id,
        count(*)                                              as candidates_evaluated,
        count(*) filter (where not c.passes_rel_10_specialist) as failed_specialist,
        count(*) filter (where not c.passes_rel_11_language)   as failed_language,
        count(*) filter (where not c.passes_rel_08_territory)  as failed_territory,
        count(*) filter (where not c.passes_rel_09_pool)       as failed_pool,
        count(*) filter (where c.passes_rel_13_hard_limit is false) as failed_hard_limit,   -- evaluated failures only
        count(*) filter (where not c.passes_rel_12_snapshot)   as failed_snapshot,
        count(*) filter (where not c.passes_rel_14_exclusion)  as failed_exclusion,
        count(*) filter (where not c.passes_rel_06_employment) as failed_employment,
        count(*) filter (where not c.passes_rel_07_availability) as failed_availability,
        -- candidates that fail only capacity, only language or only the specialist rule
        count(*) filter (where c.passes_rel_06_employment and c.passes_rel_07_availability and c.passes_rel_08_territory
                           and c.passes_rel_09_pool and c.passes_rel_10_specialist and c.passes_rel_11_language
                           and c.passes_rel_14_exclusion and not (c.passes_rel_12_snapshot and coalesce(c.passes_rel_13_hard_limit, false)))
                                                               as blocked_only_by_capacity,
        count(*) filter (where c.passes_rel_06_employment and c.passes_rel_07_availability and c.passes_rel_08_territory
                           and c.passes_rel_09_pool and c.passes_rel_10_specialist and c.passes_rel_12_snapshot
                           and coalesce(c.passes_rel_13_hard_limit, false) and c.passes_rel_14_exclusion and not c.passes_rel_11_language)
                                                               as blocked_only_by_language,
        count(*) filter (where c.passes_rel_06_employment and c.passes_rel_07_availability and c.passes_rel_08_territory
                           and c.passes_rel_09_pool and c.passes_rel_11_language and c.passes_rel_12_snapshot
                           and coalesce(c.passes_rel_13_hard_limit, false) and c.passes_rel_14_exclusion and not c.passes_rel_10_specialist)
                                                               as blocked_only_by_specialist,
        count(*) filter (where c.passes_rel_06_employment and c.passes_rel_07_availability and c.passes_rel_10_specialist
                           and c.passes_rel_11_language and c.passes_rel_12_snapshot and coalesce(c.passes_rel_13_hard_limit, false)
                           and c.passes_rel_14_exclusion and not (c.passes_rel_08_territory and c.passes_rel_09_pool))
                                                               as blocked_only_by_territory,
        count(*) filter (where c.passes_rel_06_employment and c.passes_rel_07_availability and c.passes_rel_08_territory
                           and c.passes_rel_09_pool and c.passes_rel_10_specialist and c.passes_rel_11_language
                           and c.passes_rel_12_snapshot and coalesce(c.passes_rel_13_hard_limit, false) and not c.passes_rel_14_exclusion)
                                                               as blocked_only_by_exclusion
    from {{ ref('int_lead_routing__candidate_eligibility') }} c
    where not exists (select 1 from {{ ref('int_lead_routing__candidate_ranking') }} k where k.decision_id = c.decision_id)
    group by 1
),

classified as (
    select
        p.decision_id, p.lead_id, p.as_of_timestamp, p.as_of_date, p.decision_trigger, p.decision_type,
        p.territory_id, p.territory_type, p.specialist_requirement_level, p.stated_service_language,
        p.is_capacity_warm_up_period, p.recommended_priority_band, p.priority_eligibility,
        'lead_gate'                               as queue_source,
        p.lead_gate_queue_code                    as queue_code,
        p.failed_lead_rule_codes                  as failed_rule_codes,
        0                                         as candidates_evaluated,
        0                                         as eligible_candidate_count
    from population p
    where not p.proceeds_to_candidate_evaluation

    union all

    select
        p.decision_id, p.lead_id, p.as_of_timestamp, p.as_of_date, p.decision_trigger, p.decision_type,
        p.territory_id, p.territory_type, p.specialist_requirement_level, p.stated_service_language,
        p.is_capacity_warm_up_period, p.recommended_priority_band, p.priority_eligibility,
        'no_eligible_candidate'                   as queue_source,
        case
            when n.blocked_only_by_specialist > 0 or p.specialist_requirement_level = 'mandatory' then 'Q-NSP'
            when n.blocked_only_by_language   > 0                                                 then 'Q-LNG'
            when n.blocked_only_by_capacity   > 0                                                 then 'Q-CAP'
            when n.blocked_only_by_territory  > 0 or n.failed_territory = n.candidates_evaluated   then 'Q-TER'
            when n.blocked_only_by_exclusion  > 0                                                 then 'Q-MOR'
            else 'Q-MAN'
        end                                       as queue_code,
        trim(
            case when n.failed_specialist   > 0 then 'REL-10 ' else '' end ||
            case when n.failed_language     > 0 then 'REL-11 ' else '' end ||
            case when n.failed_snapshot     > 0 then 'REL-12 ' else '' end ||
            case when n.failed_hard_limit   > 0 then 'REL-13 ' else '' end ||
            case when n.failed_territory    > 0 then 'REL-08 ' else '' end ||
            case when n.failed_pool         > 0 then 'REL-09 ' else '' end ||
            case when n.failed_availability > 0 then 'REL-07 ' else '' end ||
            case when n.failed_employment   > 0 then 'REL-06 ' else '' end ||
            case when n.failed_exclusion    > 0 then 'REL-14'  else '' end) as failed_rule_codes,
        n.candidates_evaluated,
        0                                         as eligible_candidate_count
    from population p
    join no_eligible_candidate n on n.decision_id = p.decision_id
    where p.proceeds_to_candidate_evaluation
)

select
    c.decision_id,
    c.lead_id,
    c.as_of_timestamp,
    c.as_of_date,
    c.decision_trigger,
    c.decision_type,
    c.territory_id,
    c.territory_type,
    c.specialist_requirement_level,
    c.stated_service_language,
    c.is_capacity_warm_up_period,
    c.priority_eligibility,
    c.recommended_priority_band,
    c.queue_source,
    c.queue_code,
    q.queue_name,
    q.queue_reason,
    c.failed_rule_codes,
    q.queue_owner_role,
    q.queue_sla_id,
    q.queue_sla_target,
    q.next_action,
    q.escalation_path,
    c.candidates_evaluated,
    c.eligible_candidate_count,
    '{{ var("lead_routing_rule_version") }}' as rule_version
from classified c
join {{ ref('routing_queue_rules') }} q on q.queue_code = c.queue_code
