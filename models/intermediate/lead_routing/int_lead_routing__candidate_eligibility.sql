-- Candidate eligibility: every mandatory candidate-level rule (REL-06 to REL-14) evaluated for every RM in the
-- candidate universe, at every decision that passed the lead-level gates.
-- Grain: one row per decision_id x rm_id (primary key).
--
-- Candidate universe: an RM enters it when it holds a territory relationship to the lead's territory in any effective
-- period, and additionally, on a mandatory-specialist lead, when it holds a primary specialization in that industry in
-- any effective period. REL-08 (effective territory coverage on the decision date) and REL-10 (effective specialization
-- on the decision date) are then evaluated per candidate, so a lapsed or not-yet-effective relationship is a visible
-- failure. RMs with no relationship to the territory at all are structurally out of scope rather than per-decision
-- exclusions; they are counted in structurally_excluded_rm_count and are not enumerated row by row.
--
-- National specialist pool (TER-10): when a mandatory-specialist lead has no eligible regional candidate, RMs holding
-- effective coverage of the national specialist territory and the required primary specialization become eligible as a
-- declared fallback stage. The fallback is evaluated only after the regional stage, so a national-pool RM never
-- competes with an eligible regional RM on preference.
--
-- Capacity is taken from the most recent valid snapshot with snapshot_as_of_utc at or before the decision instant;
-- a later snapshot is never used, and a candidate with no snapshot fails REL-12. REL-07 is not evaluated when no
-- snapshot exists, because availability is then unknown rather than bad: REL-12 is the rule that fails, so the
-- decision is correctly held in the capacity queue rather than being reported as an availability problem.
-- Eligibility is mandatory and absolute: an RM that fails any rule here can never be recommended, whatever its
-- preference score. As-of rule: every rule uses only state at or before the decision instant.
with decisions as (
    select * from {{ ref('int_lead_routing__routing_decision_population') }}
    where proceeds_to_candidate_evaluation
),

thresholds as (
    select max(case when parameter = 'capacity_hard_limit_ratio' then cast(value as double) end) as hard_limit,
           max(case when parameter = 'capacity_warning_ratio'    then cast(value as double) end) as warning_ratio
    from {{ ref('routing_evaluation_thresholds') }}
),

rm_count as (select count(*) as total_rms from {{ ref('stg_crm__relationship_manager') }}),

national_pool as (
    select territory_id as national_territory_id
    from {{ ref('stg_crm__territory') }} where territory_type = 'national_specialist'
),

universe as (
    select d.decision_id, t.rm_id, 'territory_relationship' as universe_reason
    from decisions d
    join {{ ref('stg_crm__rm_territory') }} t on t.territory_id = d.territory_id
    union
    select d.decision_id, sp.rm_id, 'mandatory_specialization'
    from decisions d
    join {{ ref('stg_crm__rm_specialization') }} sp
      on sp.industry_code = d.industry_code and sp.specialization_role = 'primary'
    where d.specialist_requirement_level = 'mandatory'
),

universe_distinct as (
    select decision_id, rm_id, min(universe_reason) as universe_reason
    from universe group by 1, 2
),

candidates as (
    select
        d.decision_id, d.lead_id, d.as_of_timestamp, d.as_of_date, d.decision_type, d.decision_trigger,
        d.territory_id, d.territory_type, d.size_segment, d.specialist_requirement_level, d.industry_code,
        d.stated_service_language, d.prior_owner_rm_id, d.last_known_owner_rm_id,
        u.rm_id, u.universe_reason,
        r.rm_segment, r.supported_service_languages, r.base_capacity_points,
        r.employment_start_date, r.employment_end_date, r.active_flag
    from decisions d
    join universe_distinct u on u.decision_id = d.decision_id
    join {{ ref('stg_crm__relationship_manager') }} r on r.rm_id = u.rm_id
),

territory_fit as (
    select c.decision_id, c.rm_id,
           case when max(case when t.coverage_role = 'primary' then 1 else 0 end) = 1 then 'primary' else 'backup' end
               as territory_coverage_role,
           count(*) as effective_territory_rows
    from candidates c
    join {{ ref('stg_crm__rm_territory') }} t
      on t.rm_id = c.rm_id and t.territory_id = c.territory_id
     and t.valid_from <= c.as_of_date and (t.valid_to is null or t.valid_to > c.as_of_date)
    group by 1, 2
),

specialization_fit as (
    select
        c.decision_id, c.rm_id,
        max(case when sp.specialization_role = 'primary' then 1 else 0 end) = 1 as holds_primary_specialization,
        true                                                                     as holds_any_specialization
    from candidates c
    join {{ ref('stg_crm__rm_specialization') }} sp
      on sp.rm_id = c.rm_id and sp.industry_code = c.industry_code
     and sp.valid_from <= c.as_of_date and (sp.valid_to is null or sp.valid_to > c.as_of_date)
    group by 1, 2
),

national_fit as (
    select c.decision_id, c.rm_id,
           case when max(case when t.coverage_role = 'primary' then 1 else 0 end) = 1 then 'primary' else 'backup' end
               as national_coverage_role
    from candidates c
    cross join national_pool np
    join {{ ref('stg_crm__rm_territory') }} t
      on t.rm_id = c.rm_id and t.territory_id = np.national_territory_id
     and t.valid_from <= c.as_of_date and (t.valid_to is null or t.valid_to > c.as_of_date)
    group by 1, 2
),

declines as (
    select c.decision_id, c.rm_id
    from candidates c
    join {{ ref('stg_crm__lead_assignment') }} a
      on a.lead_id = c.lead_id and a.rm_id = c.rm_id
     and a.declined_at is not null and a.declined_at < c.as_of_timestamp
    group by 1, 2
),

with_capacity as (
    select c.*, s.capacity_snapshot_id, s.snapshot_date, s.snapshot_as_of_utc, s.availability_status,
           s.available_capacity_points, s.weighted_workload_points, s.utilization_ratio, s.capacity_state
    from candidates c
    asof left join {{ ref('stg_crm__rm_capacity_snapshot') }} s
      on s.rm_id = c.rm_id and s.snapshot_as_of_utc <= c.as_of_timestamp
),

evaluated as (
    select
        w.*,
        tf.territory_coverage_role                       as regional_coverage_role,
        nf.national_coverage_role,
        coalesce(sf.holds_primary_specialization, false) as holds_primary_specialization,
        coalesce(sf.holds_any_specialization, false)     as holds_any_specialization,
        (dc.rm_id is not null)                           as declined_this_lead,
        th.hard_limit,
        th.warning_ratio,
        rc.total_rms,
        (w.active_flag and w.employment_start_date <= w.as_of_date
         and (w.employment_end_date is null or w.employment_end_date > w.as_of_date))        as passes_rel_06_employment,
        -- REL-07 is skipped when no snapshot exists: availability is unknown, and REL-12 is the rule that fails.
        (w.availability_status is null
         or w.availability_status not in ('planned_leave', 'unavailable'))                   as passes_rel_07_availability,
        (case when w.specialist_requirement_level = 'mandatory'
              then coalesce(sf.holds_primary_specialization, false) else true end)           as passes_rel_10_specialist,
        (case when w.stated_service_language in ('en', 'fr')
              then ('|' || w.supported_service_languages || '|') like ('%|' || w.stated_service_language || '|%')
              else true end)                                                                 as passes_rel_11_language,
        -- REL-12 is the capacity-data-availability rule: a snapshot must exist AND carry a usable utilization.
        (w.capacity_snapshot_id is not null and w.utilization_ratio is not null)             as passes_rel_12_snapshot,
        -- REL-13 is evaluated only when capacity can actually be established. When it cannot, the rule is NOT
        -- EVALUATED (null) rather than failed: a missing snapshot is a capacity-data problem (REL-12), never a
        -- hard-capacity breach. A REL-13 failure means a calculable utilization at or above the hard limit.
        (case when w.capacity_snapshot_id is not null and w.utilization_ratio is not null
              then cast(w.utilization_ratio as double) < th.hard_limit end)                  as passes_rel_13_hard_limit,
        ((dc.rm_id is null)
         and (w.decision_type <> 'reassignment' or w.prior_owner_rm_id is null or w.rm_id <> w.prior_owner_rm_id))
                                                                                             as passes_rel_14_exclusion
    from with_capacity w
    cross join thresholds th
    cross join rm_count rc
    left join territory_fit tf      on tf.decision_id = w.decision_id and tf.rm_id = w.rm_id
    left join national_fit nf       on nf.decision_id = w.decision_id and nf.rm_id = w.rm_id
    left join specialization_fit sf on sf.decision_id = w.decision_id and sf.rm_id = w.rm_id
    left join declines dc           on dc.decision_id = w.decision_id and dc.rm_id = w.rm_id
),

-- Stage 1: the regional candidate pool. Every mandatory rule except the territory rule is evaluated first, so the
-- national fallback can be decided from the regional result alone.
staged as (
    select
        e.*,
        (e.passes_rel_06_employment and e.passes_rel_07_availability and e.passes_rel_10_specialist
         and e.passes_rel_11_language and e.passes_rel_12_snapshot
         and coalesce(e.passes_rel_13_hard_limit, false)
         and e.passes_rel_14_exclusion)                                           as passes_non_territory_rules,
        (e.regional_coverage_role is not null)                                    as covers_lead_territory,
        (e.national_coverage_role is not null)                                    as covers_national_pool
    from evaluated e
),

fallback_decided as (
    select
        s.*,
        max(case when s.covers_lead_territory and s.passes_non_territory_rules then 1 else 0 end)
            over (partition by s.decision_id) = 1                                 as decision_has_regional_candidate
    from staged s
),

resolved as (
    select
        f.*,
        (f.specialist_requirement_level = 'mandatory' and not f.decision_has_regional_candidate
         and f.covers_national_pool and f.holds_primary_specialization)           as admitted_by_national_fallback,
        case when f.covers_lead_territory then 'regional'
             when f.specialist_requirement_level = 'mandatory' and not f.decision_has_regional_candidate
                  and f.covers_national_pool and f.holds_primary_specialization   then 'national_specialist_fallback'
             else 'not_covered' end                                               as eligibility_stage,
        (f.covers_lead_territory
         or (f.specialist_requirement_level = 'mandatory' and not f.decision_has_regional_candidate
             and f.covers_national_pool and f.holds_primary_specialization))      as passes_rel_08_territory,
        (case when f.territory_type in ('remote_service', 'national_specialist')
              then f.covers_lead_territory else true end)                         as passes_rel_09_pool,
        coalesce(f.regional_coverage_role,
                 case when f.specialist_requirement_level = 'mandatory' and not f.decision_has_regional_candidate
                           and f.covers_national_pool and f.holds_primary_specialization
                      then 'national_pool' end)                                   as territory_coverage_role
    from fallback_decided f
),

counted as (
    select r.*, count(*) over (partition by r.decision_id) as candidate_universe_size from resolved r
)

select
    decision_id,
    rm_id,
    lead_id,
    as_of_timestamp,
    as_of_date,
    decision_type,
    decision_trigger,
    universe_reason,
    candidate_universe_size,
    total_rms - candidate_universe_size                       as structurally_excluded_rm_count,
    territory_id,
    territory_type,
    territory_coverage_role,
    eligibility_stage,
    covers_lead_territory,
    covers_national_pool,
    admitted_by_national_fallback,
    decision_has_regional_candidate,
    rm_segment,
    size_segment,
    specialist_requirement_level,
    industry_code,
    holds_primary_specialization,
    holds_any_specialization,
    stated_service_language,
    supported_service_languages,
    prior_owner_rm_id,
    last_known_owner_rm_id,
    declined_this_lead,
    capacity_snapshot_id,
    snapshot_date,
    snapshot_as_of_utc                                        as capacity_snapshot_as_of_utc,
    availability_status,
    cast(available_capacity_points as double)                 as capacity_limit_points,
    cast(weighted_workload_points as double)                  as weighted_workload_points,
    cast(utilization_ratio as double)                         as utilization_ratio_at_decision,
    capacity_state,
    cast(available_capacity_points as double) - cast(weighted_workload_points as double) as remaining_capacity_points,
    (cast(utilization_ratio as double) >= warning_ratio)      as capacity_warning_at_decision,
    (cast(utilization_ratio as double) >= hard_limit)         as capacity_at_or_above_hard_limit,
    (capacity_state = 'approved_exception')                   as capacity_exception_eligible,
    passes_rel_06_employment,
    passes_rel_07_availability,
    passes_rel_08_territory,
    passes_rel_09_pool,
    passes_rel_10_specialist,
    passes_rel_11_language,
    passes_rel_12_snapshot,
    passes_rel_13_hard_limit,
    (passes_rel_13_hard_limit is not null)                     as rel_13_hard_limit_evaluated,
    passes_rel_14_exclusion,
    (passes_rel_06_employment and passes_rel_07_availability and passes_rel_08_territory and passes_rel_09_pool
     and passes_rel_10_specialist and passes_rel_11_language and passes_rel_12_snapshot
     and coalesce(passes_rel_13_hard_limit, false)
     and passes_rel_14_exclusion)                             as is_eligible,
    trim(
        case when not passes_rel_06_employment   then 'REL-06 ' else '' end ||
        case when not passes_rel_07_availability then 'REL-07 ' else '' end ||
        case when not passes_rel_08_territory    then 'REL-08 ' else '' end ||
        case when not passes_rel_09_pool         then 'REL-09 ' else '' end ||
        case when not passes_rel_10_specialist   then 'REL-10 ' else '' end ||
        case when not passes_rel_11_language     then 'REL-11 ' else '' end ||
        case when not passes_rel_12_snapshot     then 'REL-12 ' else '' end ||
        case when passes_rel_13_hard_limit is false then 'REL-13 ' else '' end ||
        case when not passes_rel_14_exclusion    then 'REL-14'  else '' end) as failed_rule_codes,
    '{{ var("lead_routing_rule_version") }}'                  as rule_version
from counted
