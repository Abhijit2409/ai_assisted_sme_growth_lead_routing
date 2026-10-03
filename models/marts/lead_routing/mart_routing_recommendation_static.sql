-- One recommendation per routing decision: either exactly one recommended RM or exactly one controlled queue.
-- Grain: one row per lead per routing-decision timestamp. Primary key: decision_id.
-- Source: int_lead_routing__candidate_ranking (rank 1) and int_lead_routing__queue_outcome, over the full decision
-- population in int_lead_routing__routing_decision_population.
-- As-of rule: the recommendation uses only information available at or before as_of_timestamp.
-- The historical_* columns are a comparison output produced after the recommendation exists. They are never read by any
-- upstream model: nothing in the priority, eligibility, preference, ranking or queue lineage joins the historical
-- selected RM, the historical routing outcome, the historical match flags or any opportunity or ground-truth record.
-- Limitations: the historical assignment is a synthetic policy record, not a correct label. Differences are
-- rule-adherence and operational-exposure comparisons, never evidence of better conversion, revenue or experience.
with population as (
    select * from {{ ref('int_lead_routing__routing_decision_population') }}
),

winner as (
    select * from {{ ref('int_lead_routing__candidate_ranking') }} where is_recommended_candidate
),

-- comparison only, joined after the recommendation is complete
historical as (
    select
        'DEC-' || d.routing_decision_id as decision_id,
        d.selected_rm_id                as historical_selected_rm_id,
        d.routing_outcome               as historical_routing_outcome,
        d.territory_match_flag          as historical_territory_match_flag,
        d.specialist_match_flag         as historical_specialist_match_flag,
        d.language_match_flag           as historical_language_match_flag,
        d.segment_match_flag            as historical_segment_match_flag,
        d.capacity_utilization_at_decision as historical_capacity_utilization,
        d.candidate_count               as historical_candidate_count,
        a.queue_code                    as historical_queue_code,
        a.manual_override_flag          as historical_manual_override_flag,
        a.capacity_exception_flag       as historical_capacity_exception_flag
    from {{ ref('stg_crm__routing_decision') }} d
    left join {{ ref('stg_crm__lead_assignment') }} a
           on a.routing_decision_id = d.routing_decision_id and a.assignment_role = 'primary'
),

historical_territory_fit as (
    select
        h.decision_id,
        exists (select 1 from {{ ref('stg_crm__rm_territory') }} t
                 where t.rm_id = h.historical_selected_rm_id and t.territory_id = p.territory_id
                   and t.valid_from <= p.as_of_date and (t.valid_to is null or t.valid_to > p.as_of_date))
                                                                          as historical_rm_covers_territory,
        exists (select 1 from {{ ref('stg_crm__rm_specialization') }} sp
                 where sp.rm_id = h.historical_selected_rm_id and sp.industry_code = p.industry_code
                   and sp.specialization_role = 'primary'
                   and sp.valid_from <= p.as_of_date and (sp.valid_to is null or sp.valid_to > p.as_of_date))
                                                                          as historical_rm_holds_primary_specialization,
        exists (select 1 from {{ ref('stg_crm__rm_specialization') }} sp
                 where sp.rm_id = h.historical_selected_rm_id and sp.industry_code = p.industry_code
                   and sp.valid_from <= p.as_of_date and (sp.valid_to is null or sp.valid_to > p.as_of_date))
                                                                          as historical_rm_holds_any_specialization
    from historical h
    join population p on p.decision_id = h.decision_id
    where h.historical_selected_rm_id is not null
)

select
    p.decision_id,
    p.lead_id,
    p.as_of_timestamp,
    p.as_of_date,
    p.decision_trigger,
    p.decision_type,
    p.lead_status_at_decision,
    p.is_capacity_warm_up_period,
    p.territory_id,
    p.territory_type,
    p.cma_code_sgc2021,
    p.size_segment,
    p.industry_code,
    p.specialist_requirement_level,
    p.stated_service_language,
    p.priority_eligibility,
    p.recommended_priority_score,
    p.recommended_priority_band,

    case when w.rm_id is not null then 'rm_assignment' else 'queue' end          as recommendation_outcome,
    w.rm_id                                                                       as recommended_rm_id,
    w.preference_score                                                            as recommended_preference_score,
    w.eligible_candidate_count,
    w.separating_tiebreak_id,
    w.tiebreak_beyond_preference_applied,
    w.second_preference_score                                                     as runner_up_preference_score,
    w.territory_coverage_role                                                     as recommended_territory_coverage_role,
    w.eligibility_stage                                                           as recommended_eligibility_stage,
    w.admitted_by_national_fallback                                               as recommended_via_national_pool,
    w.holds_primary_specialization                                                as recommended_rm_holds_primary_specialization,
    w.holds_any_specialization                                                    as recommended_rm_holds_any_specialization,
    w.rm_segment                                                                  as recommended_rm_segment,
    w.capacity_snapshot_id                                                        as recommended_capacity_snapshot_id,
    w.capacity_snapshot_as_of_utc                                                 as recommended_capacity_snapshot_as_of_utc,
    w.availability_status                                                         as recommended_rm_availability_status,
    w.weighted_workload_points                                                    as recommended_weighted_workload_points,
    w.capacity_limit_points                                                       as recommended_capacity_limit_points,
    w.utilization_ratio_at_decision                                               as recommended_utilization_ratio,
    w.remaining_capacity_points                                                   as recommended_remaining_capacity_points,
    w.capacity_warning_at_decision                                                as recommended_capacity_warning,
    w.capacity_state                                                              as recommended_capacity_state,
    w.capacity_exception_eligible                                                 as recommended_capacity_exception_eligible,

    q.queue_code                                                                  as recommended_queue_code,
    q.queue_name                                                                  as recommended_queue_name,
    q.queue_reason,
    q.queue_source,
    q.failed_rule_codes                                                           as queue_failed_rule_codes,
    q.queue_owner_role,
    q.queue_sla_id,
    q.queue_sla_target,
    q.next_action                                                                 as queue_next_action,
    q.escalation_path                                                             as queue_escalation_path,
    q.candidates_evaluated                                                        as queue_candidates_evaluated,

    case
        when w.rm_id is not null then
            'Recommended ' || w.rm_id || ': highest preference score ' || cast(w.preference_score as varchar)
            || ' among ' || cast(w.eligible_candidate_count as varchar)
            || ' eligible candidates, separated by ' || w.separating_tiebreak_id
            || '; territory coverage ' || coalesce(w.territory_coverage_role, 'none')
            || ', utilization ' || cast(w.utilization_ratio_at_decision as varchar)
            || ' from snapshot ' || w.capacity_snapshot_id || '.'
        else
            'Queued to ' || q.queue_code || ' (' || q.queue_name || '): ' || q.queue_reason
            || ' Failed rules: ' || coalesce(nullif(q.failed_rule_codes, ''), 'none recorded')
            || '. Owner: ' || q.queue_owner_role || '. Next action: ' || q.next_action || '.'
    end                                                                           as recommendation_reason,

    -- comparison only, never an input
    h.historical_selected_rm_id,
    h.historical_routing_outcome,
    h.historical_queue_code,
    h.historical_manual_override_flag,
    h.historical_capacity_exception_flag,
    h.historical_capacity_utilization,
    h.historical_candidate_count,
    (w.rm_id is not null and h.historical_selected_rm_id is not null and w.rm_id = h.historical_selected_rm_id)
                                                                                  as same_rm_as_historical,
    hf.historical_rm_covers_territory,
    hf.historical_rm_holds_primary_specialization,
    hf.historical_rm_holds_any_specialization,
    p.rule_version
from population p
left join winner w                   on w.decision_id = p.decision_id
left join {{ ref('int_lead_routing__queue_outcome') }} q on q.decision_id = p.decision_id
left join historical h               on h.decision_id = p.decision_id
left join historical_territory_fit hf on hf.decision_id = p.decision_id
