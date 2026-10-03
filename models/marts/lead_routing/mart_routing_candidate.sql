-- Every RM considered at a routing decision, with the mandatory-rule outcome and, for eligible RMs, the full
-- preference breakdown and rank.
-- Grain: one row per decision_id x rm_id among the candidates evaluated. Primary key: (decision_id, rm_id).
-- Source: int_lead_routing__candidate_eligibility joined to int_lead_routing__candidate_ranking.
-- As-of rule: capacity comes from the most recent snapshot with capacity_snapshot_as_of_utc at or before the decision;
-- territory and specialization coverage is tested against their effective dates on the decision date.
-- Limitations: eligibility is absolute, so a high preference score can never make an ineligible RM recommendable.
-- Fictional bank, hypothetical rules, entirely synthetic data.
select
    c.decision_id,
    c.rm_id,
    c.lead_id,
    c.as_of_timestamp,
    c.as_of_date,
    c.decision_trigger,
    c.decision_type,
    c.territory_id,
    c.territory_coverage_role,
    c.eligibility_stage,
    c.covers_lead_territory,
    c.covers_national_pool,
    c.admitted_by_national_fallback,
    c.decision_has_regional_candidate,
    c.candidate_universe_size,
    c.structurally_excluded_rm_count,
    c.universe_reason,
    c.rm_segment,
    c.size_segment,
    c.specialist_requirement_level,
    c.holds_primary_specialization,
    c.holds_any_specialization,
    c.stated_service_language,
    c.declined_this_lead,

    c.capacity_snapshot_id,
    c.capacity_snapshot_as_of_utc,
    c.snapshot_date                                as capacity_snapshot_date,
    c.availability_status,
    c.weighted_workload_points,
    c.capacity_limit_points,
    c.utilization_ratio_at_decision,
    c.capacity_state,
    c.capacity_warning_at_decision,
    c.capacity_at_or_above_hard_limit,
    c.remaining_capacity_points,
    c.capacity_exception_eligible,

    c.passes_rel_06_employment,
    c.passes_rel_07_availability,
    c.passes_rel_08_territory,
    c.passes_rel_09_pool,
    c.passes_rel_10_specialist,
    c.passes_rel_11_language,
    c.passes_rel_12_snapshot,
    c.passes_rel_13_hard_limit,
    c.rel_13_hard_limit_evaluated,
    c.passes_rel_14_exclusion,
    c.is_eligible,
    nullif(c.failed_rule_codes, '')                as failed_rule_codes,

    p.n01_territory_role,
    p.n02_specialist_fit,
    p.n03_segment_fit,
    p.n04_continuity,
    p.n05_headroom,
    p.n06_balance,
    p.n07_language_fit,
    p.pref_contribution_rpf_01,
    p.pref_contribution_rpf_02,
    p.pref_contribution_rpf_03,
    p.pref_contribution_rpf_04,
    p.pref_contribution_rpf_05,
    p.pref_contribution_rpf_06,
    p.pref_contribution_rpf_07,
    p.preference_score,
    p.candidate_rank,
    p.is_recommended_candidate,
    p.eligible_candidate_count,
    p.separating_tiebreak_id,
    p.tiebreak_beyond_preference_applied,
    c.rule_version
from {{ ref('int_lead_routing__candidate_eligibility') }} c
left join {{ ref('int_lead_routing__candidate_ranking') }} p
       on p.decision_id = c.decision_id and p.rm_id = c.rm_id
