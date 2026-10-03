-- Routing decision population: the lead-level mandatory gates (REL-01 to REL-05) evaluated before any RM is considered.
-- Grain: one row per decision_id (primary key).
-- A decision that fails a lead-level gate never reaches candidate evaluation and goes directly to its declared queue.
-- As-of rule: inherited from int_lead_routing__priority_features_as_of.
select
    f.decision_id,
    f.lead_id,
    f.as_of_timestamp,
    f.as_of_date,
    f.decision_trigger,
    f.decision_type,
    f.lead_status_at_decision,
    f.is_capacity_warm_up_period,
    f.territory_id,
    f.territory_type,
    f.cma_code_sgc2021,
    f.province_code,
    f.geography_status,
    f.size_segment,
    f.specialist_requirement_level,
    f.industry_code,
    f.stated_service_language,
    f.prior_owner_rm_id,
    f.last_known_owner_rm_id,
    s.priority_eligibility,
    s.recommended_priority_score,
    s.recommended_priority_band,

    (s.priority_eligibility = 'score_eligible' and s.recommended_priority_band is not null) as passes_rel_01_routable,
    (not f.in_unresolved_duplicate_group and f.lead_status_at_decision <> 'DUPLICATE_REVIEW') as passes_rel_02_duplicate,
    (f.lead_status_at_decision <> 'VALIDATION_PENDING')                                      as passes_rel_03_remediation,
    (f.geography_status in ('resolved_cma', 'resolved_province_remainder'))                  as passes_rel_04_footprint,
    (f.territory_id is not null)                                                             as passes_rel_05_territory,

    -- Queue selection follows the priority-eligibility outcome first, then geography, then the generic review queue,
    -- so that the reason given to the queue owner is the one that must actually be acted on. An ineligible lead goes
    -- to general manual review even when its geography is also unresolved: telling the Regional Sales Manager to
    -- resolve a territory for a lead that will never be worked is the wrong action (PEL-01 closes it with a
    -- disposition instead).
    case
        when s.priority_eligibility in ('duplicate_review', 'merged')                           then 'Q-DUP'
        when s.priority_eligibility = 'data_remediation'                                        then 'Q-DRM'
        when f.geography_status = 'out_of_footprint'                                            then 'Q-OOF'
        when s.priority_eligibility in ('invalid', 'withdrawn', 'terminal', 'not_yet_routable') then 'Q-MAN'
        when s.priority_eligibility = 'manual_review'
             or f.territory_id is null or f.geography_status in ('conflict', 'missing')         then 'Q-TER'
        when not (s.priority_eligibility = 'score_eligible' and s.recommended_priority_band is not null) then 'Q-MAN'
    end                                                                                      as lead_gate_queue_code,

    trim(
        case when not (s.priority_eligibility = 'score_eligible' and s.recommended_priority_band is not null) then 'REL-01 ' else '' end ||
        case when f.in_unresolved_duplicate_group or f.lead_status_at_decision = 'DUPLICATE_REVIEW' then 'REL-02 ' else '' end ||
        case when f.lead_status_at_decision = 'VALIDATION_PENDING' then 'REL-03 ' else '' end ||
        case when f.geography_status not in ('resolved_cma', 'resolved_province_remainder') then 'REL-04 ' else '' end ||
        case when f.territory_id is null then 'REL-05' else '' end)                          as failed_lead_rule_codes,

    (s.priority_eligibility = 'score_eligible' and s.recommended_priority_band is not null
     and not f.in_unresolved_duplicate_group and f.lead_status_at_decision <> 'DUPLICATE_REVIEW'
     and f.lead_status_at_decision <> 'VALIDATION_PENDING'
     and f.geography_status in ('resolved_cma', 'resolved_province_remainder')
     and f.territory_id is not null)                                                         as proceeds_to_candidate_evaluation,
    f.rule_version
from {{ ref('int_lead_routing__priority_features_as_of') }} f
join {{ ref('int_lead_routing__lead_priority_score') }} s on s.decision_id = f.decision_id
