-- The operational RM recommendation: what the declared rules would actually assign if capacity were consumed.
-- Grain: one row per lead per routing-decision timestamp. Primary key: decision_id.
-- Source: int_lead_routing__operational_assignment (the sequential capacity-ledger simulation), enriched with the
-- queue catalogue so every held lead carries an owner, an SLA and a next action.
--
-- This is the primary recommendation. It differs from mart_routing_recommendation_static in one decisive way:
-- every assignment it makes consumes the chosen RM's simulated capacity, so an RM that has already received work
-- earlier in the run has less room for the next lead. The static mart ranks each decision independently and never
-- consumes capacity; it remains available as a diagnostic of rule fit and must not be read as an executable plan.
--
-- As-of rule: every input is available at or before as_of_timestamp. The ledger is built strictly forward in time
-- from this model's own earlier decisions. No conversion outcome, opportunity record, ground-truth label or
-- historical selected RM takes any part in choosing the recommendation; the historical_* columns are a comparison
-- output joined after the recommendation exists.
--
-- Limitations: a simulation over hypothetical rules and entirely synthetic data. The workload a new assignment adds
-- is the declared CAP-01 weight of one point; real post-assignment load would change as the lead progresses, which
-- this simulation does not model. Nothing here is evidence of better conversion, revenue or customer experience.
with assignment as (
    select * from {{ ref('int_lead_routing__operational_assignment') }}
),

queue_rules as (
    select * from {{ ref('routing_queue_rules') }}
),

priority as (
    select decision_id, recommended_priority_band_name, recommended_first_contact_sla_id,
           base_priority_score, override_rule_id, cma_code_sgc2021, industry_code, size_segment
    from {{ ref('mart_lead_priority') }}
),

-- comparison only, joined after the recommendation is complete
historical as (
    select
        'DEC-' || d.routing_decision_id    as decision_id,
        d.selected_rm_id                   as historical_selected_rm_id,
        d.routing_outcome                  as historical_routing_outcome,
        d.capacity_utilization_at_decision as historical_capacity_utilization,
        a.queue_code                       as historical_queue_code
    from {{ ref('stg_crm__routing_decision') }} d
    left join {{ ref('stg_crm__lead_assignment') }} a
           on a.routing_decision_id = d.routing_decision_id and a.assignment_role = 'primary'
)

select
    -- identity and timing
    a.decision_id,
    a.lead_id,
    a.as_of_timestamp,
    a.as_of_date,
    a.decision_trigger,
    a.decision_type,
    a.is_capacity_warm_up_period,
    a.processing_sequence,

    -- priority
    a.priority_tier,
    a.priority_score,
    p.recommended_priority_band_name                          as priority_tier_name,
    p.recommended_first_contact_sla_id                        as first_contact_sla_id,
    a.max_sla_pressure_at_decision,

    -- the recommendation
    a.recommendation_status,
    a.recommended_rm_id,
    a.candidate_rank,
    a.preference_score,
    a.eligible_candidate_count,
    a.candidates_rejected_for_capacity,

    -- the queue result, when the lead is held
    a.queue_code,
    q.queue_name,
    a.queue_reason,
    q.queue_owner_role,
    q.queue_sla_id,
    q.queue_sla_target,
    q.next_action                                             as queue_next_action,
    q.escalation_path                                         as queue_escalation_path,

    -- the capacity ledger, in full
    a.capacity_snapshot_id,
    a.capacity_snapshot_as_of_utc,
    a.observed_workload_points_at_snapshot,
    a.simulated_workload_added_since_snapshot,
    a.workload_points_added_by_this_recommendation,
    a.simulated_post_assignment_workload_points,
    a.capacity_limit_points,
    a.simulated_post_assignment_utilization,
    a.remaining_capacity_points_after,
    a.assignment_workload_points_declared,
    a.post_assignment_limit_ratio_declared,

    -- mandatory rule results for the selected RM
    a.territory_id,
    a.territory_eligibility_result,
    a.specialist_requirement_level,
    a.specialist_eligibility_result,
    a.stated_service_language,
    a.language_eligibility_result,

    -- deterministic ordering and tie-breaks
    a.tiebreak_priority_tier_order,
    a.tiebreak_score_order,
    a.tiebreak_sla_order,
    a.separating_tiebreak_id,

    -- context
    p.cma_code_sgc2021,
    p.industry_code,
    p.size_segment,

    -- comparison outputs, produced after the recommendation exists
    h.historical_selected_rm_id,
    h.historical_routing_outcome,
    h.historical_capacity_utilization,
    h.historical_queue_code,
    (a.recommended_rm_id is not null and a.recommended_rm_id = h.historical_selected_rm_id)
                                                              as same_rm_as_historical,

    a.recommendation_basis,
    a.rule_version
from assignment a
left join queue_rules q on q.queue_code = a.queue_code
left join priority   p on p.decision_id = a.decision_id
left join historical h on h.decision_id = a.decision_id
