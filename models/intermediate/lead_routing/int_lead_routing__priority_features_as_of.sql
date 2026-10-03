-- As-of features for every routing decision.
-- Grain: one row per decision_id (primary key), from int_lead_routing__decision_population.
-- As-of rule: every field is either a static lead or prospect attribute captured at or before the decision, an external
-- public-data market attribute, or a quantity computed strictly from events before the decision instant. Nothing is read
-- from the decision outcome, from later activities, statuses, assignments, capacity snapshots or SLA results, from
-- fct_opportunity_outcome, or from data/synthetic/crm_ground_truth.
-- Hypothetical framework, fictional bank, entirely synthetic data.
with decisions as (
    select * from {{ ref('int_lead_routing__decision_population') }}
),

thresholds as (
    select
        max(case when parameter = 'mandatory_specialist_industries' then value end) as mandatory_industries,
        max(case when parameter = 'preferred_specialist_industries' then value end) as preferred_industries,
        max(case when parameter = 'excluded_industries'             then value end) as excluded_industries,
        max(case when parameter = 'eligible_employee_bands'         then value end) as eligible_bands,
        max(case when parameter = 'imminent_breach_pressure'        then cast(value as double) end) as imminent_breach_pressure
    from {{ ref('routing_evaluation_thresholds') }}
),

-- SLA clocks in flight at the decision instant: started strictly before it and not stopped before it.
-- Pressure is linear between the clock start and its business-calendar target timestamp, so the endpoints are exact.
sla_in_flight as (
    select
        d.decision_id,
        s.sla_id,
        case when s.sla_id in ('SLA-01', 'SLA-02', 'SLA-03', 'SLA-04A', 'SLA-04B', 'SLA-04C')
             then 'intake' else 'ownership' end as clock_family,
        case when epoch(s.target_at) - epoch(s.started_at) > 0
             then (epoch(d.as_of_timestamp) - epoch(s.started_at)) / (epoch(s.target_at) - epoch(s.started_at))
             else 0.0 end                       as pressure
    from decisions d
    join {{ ref('stg_crm__sla_event') }} s
      on s.lead_id = d.lead_id
     and s.started_at < d.as_of_timestamp
     and (s.stopped_at is null or s.stopped_at >= d.as_of_timestamp)
),

sla_pressure as (
    select
        decision_id,
        max(case when clock_family = 'intake'    then pressure end) as intake_sla_pressure,
        max(case when clock_family = 'ownership' then pressure end) as ownership_sla_pressure,
        max(pressure)                                               as max_sla_pressure,
        max(case when sla_id = 'SLA-10' then 1 else 0 end) = 1      as escalation_clock_in_flight,
        count(*)                                                    as sla_clocks_in_flight
    from sla_in_flight
    group by 1
),

-- Activity strictly before the decision instant.
pre_activity as (
    select
        d.decision_id,
        count(*) filter (where a.counts_as_contact_attempt)                          as contact_attempts_before_decision,
        count(*) filter (where a.activity_outcome = 'two_way_contact') > 0           as two_way_contact_before_decision,
        count(*)                                                                     as activities_before_decision,
        max(a.activity_at)                                                           as last_activity_at_before_decision
    from decisions d
    join {{ ref('stg_crm__lead_activity') }} a
      on a.lead_id = d.lead_id and a.activity_at < d.as_of_timestamp
    group by 1
),

-- Unresolved duplicate membership at the decision instant.
duplicate_state as (
    select distinct d.decision_id
    from decisions d
    join {{ ref('int_crm__duplicate_resolution') }} g
      on (g.flagged_lead_id = d.lead_id or g.matched_lead_id = d.lead_id)
     and g.flagged_at <= d.as_of_timestamp
     and (g.resolved_at is null or g.resolved_at > d.as_of_timestamp)
),

-- Ownership strictly before the decision: the RM owning the lead immediately before it, and every RM who declined it.
-- Required by RT-X03 (exclusion) and by the continuity preference. Never used as a priority-score feature and never
-- read from the decision being replayed.
prior_owner as (
    select decision_id, rm_id as prior_owner_rm_id, assignment_id as prior_assignment_id
    from (
        select
            d.decision_id, a.rm_id, a.assignment_id,
            row_number() over (partition by d.decision_id order by a.assigned_at desc, a.assignment_id desc) rn
        from decisions d
        join {{ ref('stg_crm__lead_assignment') }} a
          on a.lead_id = d.lead_id
         and a.rm_id is not null
         and a.assignment_role = 'primary'
         and a.assigned_at < d.as_of_timestamp
         and (a.ended_at is null or a.ended_at >= d.as_of_timestamp)
    ) where rn = 1
),

last_owner_any as (
    select decision_id, rm_id as last_known_owner_rm_id
    from (
        select
            d.decision_id, a.rm_id,
            row_number() over (partition by d.decision_id order by a.assigned_at desc, a.assignment_id desc) rn
        from decisions d
        join {{ ref('stg_crm__lead_assignment') }} a
          on a.lead_id = d.lead_id and a.rm_id is not null and a.assignment_role = 'primary'
         and a.assigned_at < d.as_of_timestamp
    ) where rn = 1
)

select
    d.decision_id,
    d.routing_decision_id,
    d.lead_id,
    d.as_of_timestamp,
    d.as_of_date,
    d.decision_trigger,
    d.decision_type,
    d.lead_status_at_decision,
    d.decision_sequence_for_lead,
    d.is_capacity_warm_up_period,

    -- lead attributes captured with the lead
    l.first_channel_code,
    l.stated_product_interest,
    l.stated_service_language,
    l.consent_to_contact_flag,
    l.geography_status,
    l.territory_id,
    l.cma_code_sgc2021,
    l.province_code,
    l.surviving_lead_id,
    t.territory_type,

    -- prospect attributes
    p.employee_band,
    p.industry_code,
    p.country_code,
    p.existing_relationship_flag,
    case when p.employee_band in ('1-4', '5-19') then 'micro'
         when p.employee_band in ('20-99', '100-499') then 'established'
         else 'unknown' end                                                       as size_segment,
    case when ';' || th.excluded_industries  || ';' like '%;' || coalesce(p.industry_code, 'NA') || ';%' then 'excluded'
         when ';' || th.mandatory_industries || ';' like '%;' || coalesce(p.industry_code, 'NA') || ';%' then 'mandatory'
         when ';' || th.preferred_industries || ';' like '%;' || coalesce(p.industry_code, 'NA') || ';%' then 'preferred'
         when p.industry_code is null or p.industry_code = 'UNCLASSIFIED'                                then 'unclassified'
         else 'standard' end                                                      as specialist_requirement_level,
    (p.employee_band is not null
     and ';' || th.eligible_bands || ';' like '%;' || p.employee_band || ';%')     as employee_band_in_scope,

    -- external market context (public-data marts; period-independent of the decision)
    m.market_evidence_flag,
    m.evidence_tier,
    m.market_opportunity_score,
    m.market_opportunity_classification,
    case when m.market_opportunity_score is not null       then 'scored'
         when m.market_evidence_flag = 'scale_only_limited_evidence' then 'neutral_tier3_scale_only'
         else 'neutral_missing_context' end                                       as market_context_treatment,

    -- data completeness: six declared items
    (case when l.geography_status in ('resolved_cma', 'resolved_province_remainder') then 1 else 0 end
     + case when l.territory_id is not null then 1 else 0 end
     + case when p.employee_band is not null and p.employee_band <> 'unknown' then 1 else 0 end
     + case when p.industry_code is not null and p.industry_code <> 'UNCLASSIFIED' then 1 else 0 end
     + case when l.stated_product_interest is not null and l.stated_product_interest <> 'not stated' then 1 else 0 end
     + case when l.consent_to_contact_flag then 1 else 0 end)                     as completeness_items_present,
    6                                                                             as completeness_items_checked,

    -- operational urgency inputs
    (epoch(d.as_of_timestamp) - epoch(l.captured_at)) / 3600.0                    as lead_age_hours_at_decision,
    coalesce(sp.intake_sla_pressure, 0.0)                                         as intake_sla_pressure,
    sp.intake_sla_pressure is not null                                            as intake_clock_in_flight,
    coalesce(sp.ownership_sla_pressure, 0.0)                                      as ownership_sla_pressure,
    sp.ownership_sla_pressure is not null                                         as ownership_clock_in_flight,
    coalesce(sp.max_sla_pressure, 0.0)                                            as max_sla_pressure_at_decision,
    coalesce(sp.escalation_clock_in_flight, false)                                as escalation_clock_in_flight,
    coalesce(sp.sla_clocks_in_flight, 0)                                          as sla_clocks_in_flight,
    coalesce(sp.max_sla_pressure, 0.0) >= th.imminent_breach_pressure             as imminent_sla_breach_at_decision,

    coalesce(pa.contact_attempts_before_decision, 0)                              as contact_attempts_before_decision,
    coalesce(pa.two_way_contact_before_decision, false)                           as two_way_contact_before_decision,
    coalesce(pa.activities_before_decision, 0)                                    as activities_before_decision,
    case when coalesce(pa.two_way_contact_before_decision, false)      then 'two_way_contact'
         when coalesce(pa.contact_attempts_before_decision, 0) >= 3    then 'three_or_more_attempts_no_contact'
         when coalesce(pa.contact_attempts_before_decision, 0) >= 1    then 'one_or_two_attempts_no_contact'
         else 'no_activity' end                                                   as pre_decision_engagement_state,

    -- gates
    (ds.decision_id is not null)                                                  as in_unresolved_duplicate_group,
    po.prior_owner_rm_id,
    po.prior_assignment_id,
    lo.last_known_owner_rm_id,
    d.rule_version
from decisions d
cross join thresholds th
join {{ ref('stg_crm__lead') }} l                       on l.lead_id = d.lead_id
join {{ ref('stg_crm__business_prospect') }} p          on p.prospect_id = l.prospect_id
left join {{ ref('stg_crm__territory') }} t             on t.territory_id = l.territory_id
left join {{ ref('int_crm__lead_market_context') }} m   on m.lead_id = d.lead_id
left join sla_pressure sp                               on sp.decision_id = d.decision_id
left join pre_activity pa                               on pa.decision_id = d.decision_id
left join duplicate_state ds                            on ds.decision_id = d.decision_id
left join prior_owner po                                on po.decision_id = d.decision_id
left join last_owner_any lo                             on lo.decision_id = d.decision_id
