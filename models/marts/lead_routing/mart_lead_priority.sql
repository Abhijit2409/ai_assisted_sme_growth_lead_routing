-- Recommended lead priority at every routing-decision timestamp.
-- Grain: one row per lead per routing-decision timestamp. Primary key: decision_id.
-- Source: int_lead_routing__lead_priority_score (and its upstream as-of feature model).
-- As-of rule: every field is available at or before as_of_timestamp. The source lead priority field is preserved
-- unchanged as source_priority_band; the recommendation is published separately.
-- Limitations: the decision timestamps come from the synthetic routing log, so the timing of decisions is the historical
-- policy's; only the priority is recomputed. All source leads carry source priority P2, so no comparison of recommended
-- against source bands is possible. Fictional bank, hypothetical rules, entirely synthetic data; no causal claim.
select
    s.decision_id,
    s.lead_id,
    s.as_of_timestamp,
    s.as_of_date,
    s.decision_trigger,
    s.decision_type,
    s.decision_sequence_for_lead,
    s.lead_status_at_decision,
    s.is_capacity_warm_up_period,

    l.priority_band                               as source_priority_band,

    s.priority_eligibility,
    e.eligibility_rule_id,
    e.eligibility_meaning,
    e.eligibility_next_action,
    e.eligibility_owner_role,
    s.is_scoreable,

    s.business_value_contribution,
    s.operational_urgency_contribution,
    s.base_priority_score,
    s.base_priority_band,
    s.recommended_priority_score,
    s.recommended_priority_band,
    s.override_rule_id,
    s.override_action,
    s.override_reason,
    s.component_count,

    b.band_name                                   as recommended_priority_band_name,
    b.band_meaning                                as recommended_priority_band_meaning,
    b.first_contact_sla_id                        as recommended_first_contact_sla_id,
    b.operational_expectation                     as recommended_operational_expectation,

    f.first_channel_code,
    f.stated_product_interest,
    f.employee_band,
    f.size_segment,
    f.industry_code,
    f.specialist_requirement_level,
    f.territory_id,
    f.territory_type,
    f.cma_code_sgc2021,
    f.geography_status,
    s.market_context_treatment,
    s.market_evidence_flag,
    s.evidence_tier,
    s.market_normalized_value,
    s.market_used_neutral_default,
    f.completeness_items_present,
    f.completeness_items_checked,
    f.lead_age_hours_at_decision,
    f.intake_sla_pressure,
    f.ownership_sla_pressure,
    s.max_sla_pressure_at_decision,
    s.imminent_sla_breach_at_decision,
    s.escalation_clock_in_flight,
    f.pre_decision_engagement_state,
    s.rule_version
from {{ ref('int_lead_routing__lead_priority_score') }} s
join {{ ref('int_lead_routing__priority_features_as_of') }} f on f.decision_id = s.decision_id
join {{ ref('int_lead_routing__priority_eligibility') }} e    on e.decision_id = s.decision_id
join {{ ref('stg_crm__lead') }} l                             on l.lead_id = s.lead_id
left join {{ ref('lead_priority_band_rules') }} b             on b.band_code = s.recommended_priority_band
