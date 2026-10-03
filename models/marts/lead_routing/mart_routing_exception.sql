-- Every decision that did not end in an ordinary RM recommendation, with the operational handling it requires.
-- Grain: one row per decision_id that ends in a controlled queue. Primary key: decision_id.
-- Source: int_lead_routing__queue_outcome, enriched with the candidate-pool diagnostics that explain the exception.
-- As-of rule: inherited from the queue model; every reason is evaluated at the decision instant.
-- Limitations: an exception is an operational-exposure measure of the declared rules, not evidence that the lead was
-- mishandled historically. Fictional bank, hypothetical rules, entirely synthetic data.
with pool_diagnostics as (
    select
        decision_id,
        count(*)                                                as candidates_evaluated,
        count(*) filter (where passes_rel_06_employment)        as passed_employment,
        count(*) filter (where passes_rel_07_availability)      as passed_availability,
        count(*) filter (where passes_rel_08_territory)         as passed_territory,
        count(*) filter (where passes_rel_10_specialist)        as passed_specialist,
        count(*) filter (where passes_rel_11_language)          as passed_language,
        count(*) filter (where passes_rel_12_snapshot)          as passed_snapshot,
        count(*) filter (where passes_rel_13_hard_limit)        as passed_hard_limit,
        count(*) filter (where passes_rel_14_exclusion)         as passed_exclusion,
        count(*) filter (where capacity_at_or_above_hard_limit) as candidates_at_or_above_hard_limit,
        count(*) filter (where capacity_warning_at_decision)    as candidates_at_or_above_warning,
        min(utilization_ratio_at_decision)                      as pool_min_utilization,
        max(utilization_ratio_at_decision)                      as pool_max_utilization
    from {{ ref('int_lead_routing__candidate_eligibility') }}
    group by 1
)

select
    q.decision_id,
    q.lead_id,
    q.as_of_timestamp,
    q.as_of_date,
    q.decision_trigger,
    q.decision_type,
    q.is_capacity_warm_up_period,
    q.territory_id,
    q.territory_type,
    q.specialist_requirement_level,
    q.stated_service_language,
    q.priority_eligibility,
    q.recommended_priority_band,
    q.queue_source,
    q.queue_code,
    q.queue_name,
    q.queue_reason,
    q.failed_rule_codes,
    q.queue_owner_role,
    q.queue_sla_id,
    q.queue_sla_target,
    q.next_action,
    q.escalation_path,
    coalesce(d.candidates_evaluated, 0)                 as candidates_evaluated,
    coalesce(d.passed_employment, 0)                    as candidates_passing_employment,
    coalesce(d.passed_availability, 0)                  as candidates_passing_availability,
    coalesce(d.passed_territory, 0)                     as candidates_passing_territory,
    coalesce(d.passed_specialist, 0)                    as candidates_passing_specialist,
    coalesce(d.passed_language, 0)                      as candidates_passing_language,
    coalesce(d.passed_snapshot, 0)                      as candidates_passing_snapshot,
    coalesce(d.passed_hard_limit, 0)                    as candidates_passing_hard_limit,
    coalesce(d.passed_exclusion, 0)                     as candidates_passing_exclusion,
    coalesce(d.candidates_at_or_above_hard_limit, 0)    as candidates_at_or_above_hard_limit,
    coalesce(d.candidates_at_or_above_warning, 0)       as candidates_at_or_above_warning,
    d.pool_min_utilization,
    d.pool_max_utilization,
    (q.queue_code in ('Q-MAN', 'Q-MOR', 'Q-TER', 'Q-LNG')) as requires_manual_review,
    q.rule_version
from {{ ref('int_lead_routing__queue_outcome') }} q
left join pool_diagnostics d on d.decision_id = q.decision_id
