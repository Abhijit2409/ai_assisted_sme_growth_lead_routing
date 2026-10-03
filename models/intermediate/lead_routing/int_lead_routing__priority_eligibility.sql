-- Priority eligibility, decided before any score is calculated.
-- Grain: one row per decision_id (primary key).
-- The ordered rules in seeds/lead_routing/lead_priority_eligibility_rules.csv are applied first-match-wins; only the
-- score_eligible outcome is scoreable. As-of rule: every condition uses the lead state at the decision instant.
with classified as (
select
    f.decision_id,
    f.lead_id,
    f.as_of_timestamp,
    f.decision_trigger,
    f.decision_type,
    f.lead_status_at_decision,
    case
        when f.lead_status_at_decision = 'INVALID'
             or not f.employee_band_in_scope
             or f.specialist_requirement_level = 'excluded'
             or coalesce(f.country_code, 'CA') <> 'CA'
             or f.existing_relationship_flag
             or f.geography_status = 'out_of_footprint'                                        then 'invalid'
        -- surviving_lead_id is a current lead attribute, set when the merge happens, so it is deliberately NOT used:
        -- at an earlier decision the lead was not yet merged. Only the as-of status and the as-of duplicate group count.
        when f.lead_status_at_decision = 'MERGED'                                               then 'merged'
        when f.lead_status_at_decision = 'WITHDRAWN'                                            then 'withdrawn'
        when f.lead_status_at_decision in ('CONVERTED', 'CLOSED_LOST')                          then 'terminal'
        when f.lead_status_at_decision = 'DUPLICATE_REVIEW' or f.in_unresolved_duplicate_group  then 'duplicate_review'
        when f.lead_status_at_decision = 'VALIDATION_PENDING'                                   then 'data_remediation'
        when f.geography_status in ('conflict', 'missing') or f.territory_id is null            then 'manual_review'
        when f.lead_status_at_decision = 'NEW'
             or (f.decision_type = 'initial' and f.lead_status_at_decision <> 'READY_FOR_ASSIGNMENT')
                                                                                                then 'not_yet_routable'
        else 'score_eligible'
    end                                                        as priority_eligibility,
    f.rule_version
from {{ ref('int_lead_routing__priority_features_as_of') }} f
)

select
    c.decision_id,
    c.lead_id,
    c.as_of_timestamp,
    c.decision_trigger,
    c.decision_type,
    c.lead_status_at_decision,
    c.priority_eligibility,
    r.rule_id                 as eligibility_rule_id,
    r.evaluation_order        as eligibility_rule_order,
    r.is_scoreable,
    r.outcome_meaning         as eligibility_meaning,
    r.next_action             as eligibility_next_action,
    r.owner_role              as eligibility_owner_role,
    r.source_rule_id          as eligibility_source_rule_id,
    c.rule_version
from classified c
join {{ ref('lead_priority_eligibility_rules') }} r on r.eligibility_outcome = c.priority_eligibility
