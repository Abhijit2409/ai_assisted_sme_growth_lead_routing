-- Candidate preference: the transparent comparison applied only to RMs that already passed every mandatory rule.
-- Grain: one row per decision_id x rm_id among eligible candidates (primary key).
-- Every preference contribution is published. Preference can never make an ineligible RM recommendable, because
-- ineligible candidates are filtered out before this model runs.
-- As-of rule: inherited from int_lead_routing__candidate_eligibility.
with weights as (
    select
        max(case when preference_id = 'RPF-01' then weight end) as w01,
        max(case when preference_id = 'RPF-02' then weight end) as w02,
        max(case when preference_id = 'RPF-03' then weight end) as w03,
        max(case when preference_id = 'RPF-04' then weight end) as w04,
        max(case when preference_id = 'RPF-05' then weight end) as w05,
        max(case when preference_id = 'RPF-06' then weight end) as w06,
        max(case when preference_id = 'RPF-07' then weight end) as w07
    from {{ ref('routing_preference_weight_config') }}
),

thresholds as (
    select max(case when parameter = 'preference_rounding_decimals' then cast(value as integer) end) as pref_decimals
    from {{ ref('routing_evaluation_thresholds') }}
),

eligible as (
    select * from {{ ref('int_lead_routing__candidate_eligibility') }} where is_eligible
),

pool as (
    select
        e.*,
        max(e.utilization_ratio_at_decision) over (partition by e.decision_id) as pool_max_utilization,
        min(e.utilization_ratio_at_decision) over (partition by e.decision_id) as pool_min_utilization,
        count(*) over (partition by e.decision_id)                             as eligible_candidate_count
    from eligible e
),

normalized as (
    select
        p.*,
        case when p.territory_coverage_role = 'primary' then 100.0 else 50.0 end                   as n01_territory_role,  -- backup and national_pool both take 50
        case when p.specialist_requirement_level = 'mandatory'                       then 100.0
             when p.specialist_requirement_level = 'preferred'
                  and p.holds_any_specialization                                     then 100.0
             when p.specialist_requirement_level = 'preferred'                       then  40.0
             else 70.0 end                                                                         as n02_specialist_fit,
        case when (p.size_segment = 'micro'       and p.rm_segment = 'small_business')
                or (p.size_segment = 'established' and p.rm_segment = 'commercial') then 100.0
             else 40.0 end                                                                         as n03_segment_fit,
        case when p.decision_type = 'reassignment'                                   then  50.0
             when p.last_known_owner_rm_id is null                                   then  50.0
             when p.rm_id = p.last_known_owner_rm_id                                 then 100.0
             else 0.0 end                                                                          as n04_continuity,
        least(100.0, greatest(0.0, 100.0 * (1.0 - p.utilization_ratio_at_decision)))               as n05_headroom,
        case when p.pool_max_utilization - p.pool_min_utilization <= 0 then 50.0
             else 100.0 * (p.pool_max_utilization - p.utilization_ratio_at_decision)
                        / (p.pool_max_utilization - p.pool_min_utilization) end                    as n06_balance,
        case when p.stated_service_language in ('en', 'fr') then 100.0 else 70.0 end               as n07_language_fit
    from pool p
)

select
    n.decision_id,
    n.rm_id,
    n.lead_id,
    n.as_of_timestamp,
    n.as_of_date,
    n.decision_type,
    n.decision_trigger,
    n.eligible_candidate_count,
    n.territory_id,
    n.territory_coverage_role,
    n.eligibility_stage,
    n.admitted_by_national_fallback,
    n.rm_segment,
    n.size_segment,
    n.specialist_requirement_level,
    n.holds_primary_specialization,
    n.holds_any_specialization,
    n.stated_service_language,
    n.capacity_snapshot_id,
    n.capacity_snapshot_as_of_utc,
    n.availability_status,
    n.capacity_limit_points,
    n.weighted_workload_points,
    n.utilization_ratio_at_decision,
    n.remaining_capacity_points,
    n.capacity_state,
    n.capacity_warning_at_decision,
    n.capacity_exception_eligible,
    n.pool_max_utilization,
    n.pool_min_utilization,
    n.n01_territory_role, n.n02_specialist_fit, n.n03_segment_fit, n.n04_continuity,
    n.n05_headroom, n.n06_balance, n.n07_language_fit,
    round(w.w01 * n.n01_territory_role, t.pref_decimals) as pref_contribution_rpf_01,
    round(w.w02 * n.n02_specialist_fit, t.pref_decimals) as pref_contribution_rpf_02,
    round(w.w03 * n.n03_segment_fit,    t.pref_decimals) as pref_contribution_rpf_03,
    round(w.w04 * n.n04_continuity,     t.pref_decimals) as pref_contribution_rpf_04,
    round(w.w05 * n.n05_headroom,       t.pref_decimals) as pref_contribution_rpf_05,
    round(w.w06 * n.n06_balance,        t.pref_decimals) as pref_contribution_rpf_06,
    round(w.w07 * n.n07_language_fit,   t.pref_decimals) as pref_contribution_rpf_07,
    round(((((((w.w01 * n.n01_territory_role) + (w.w02 * n.n02_specialist_fit)) + (w.w03 * n.n03_segment_fit))
             + (w.w04 * n.n04_continuity)) + (w.w05 * n.n05_headroom)) + (w.w06 * n.n06_balance))
             + (w.w07 * n.n07_language_fit), t.pref_decimals)                        as preference_score,
    '{{ var("lead_routing_rule_version") }}'                                         as rule_version
from normalized n
cross join weights w
cross join thresholds t
