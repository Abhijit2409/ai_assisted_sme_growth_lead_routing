-- One row per lead: the single place where every one-to-many acquisition source is collapsed to lead grain.
-- Grain: one row per lead. Primary key: lead_id.
--
-- Why this model exists. Five history tables fan out badly against a lead: status history reaches 12 rows per lead,
-- SLA events 27, activities 19, assignments 6, routing decisions 6 and priority decisions 6. Any mart that joined
-- them directly would multiply its lead counts. Every downstream acquisition-intelligence mart reads this model
-- instead, so a lead can be counted exactly once by construction.
--
-- Source: analytics.fct_lead (already lead grain, carrying current status and owner), dim_business_prospect,
-- dim_cma, dim_acquisition_channel, the first-contact SLA clocks, the latest priority decision, the latest
-- operational routing decision and the opportunity outcome.
--
-- As-of rule: everything is reported as at the declared reporting cutoff (2026-05-31, assumption A-018). No event
-- after the cutoff is read.
--
-- Limitations: entirely synthetic CRM data over hypothetical rules. Outcome columns are descriptive only; nothing
-- here feeds any priority or routing rule.
with params as (
    select
        max(case when parameter = 'reporting_cutoff_date'        then cast(value as date) end)  as cutoff_date,
        max(case when parameter = 'conversion_maturity_days'     then cast(value as integer) end) as conversion_maturity_days,
        max(case when parameter = 'qualification_maturity_days'  then cast(value as integer) end) as qualification_maturity_days,
        max(case when parameter = 'ageing_lead_days'             then cast(value as integer) end) as ageing_lead_days,
        max(case when parameter = 'unresolved_cma_label'         then value end)                 as unresolved_cma_label,
        max(case when parameter = 'unclassified_industry_label'  then value end)                 as unclassified_industry_label
    from {{ ref('acquisition_reporting_parameters') }}
),

lead_base as (
    select * from {{ ref('fct_lead') }}
),

-- first-contact SLA: one row per lead, taken from the SLA-06 family only
first_contact_sla as (
    select
        lead_id,
        count(*)                                                             as first_contact_clocks,
        max(case when is_breached then 1 else 0 end) = 1                     as first_contact_sla_breached,
        max(case when stopped_at is null then 1 else 0 end) = 1              as first_contact_sla_open,
        min(target_at)                                                       as first_contact_sla_target_at,
        min(sla_id)                                                          as first_contact_sla_id,
        max(responsible_role)                                                as first_contact_sla_role
    from {{ ref('fct_sla_event') }}
    where sla_id like 'SLA-06%'
    group by 1
),

-- any open breached clock of any family, for the action queue
open_breaches as (
    select lead_id, count(*) as open_breached_clocks
    from {{ ref('fct_sla_event') }}
    where is_breached and stopped_at is null
    group by 1
),

-- assignment history collapsed to lead grain
assignments as (
    select
        lead_id,
        count(*)                                                             as assignment_count,
        count(distinct rm_id) filter (where rm_id is not null)               as distinct_rms_assigned,
        min(assigned_at)                                                     as first_assigned_at_hist
    from {{ ref('fct_lead_assignment') }}
    where assignment_role = 'primary'
    group by 1
),

-- the latest priority decision for the lead, by decision instant then decision id
latest_priority as (
    select lead_id, recommended_priority_score, recommended_priority_band, recommended_priority_band_name,
           priority_eligibility, as_of_timestamp as priority_decided_at
    from (
        select p.*, row_number() over (partition by p.lead_id
                                       order by p.as_of_timestamp desc, p.decision_id desc) as rn
        from {{ ref('mart_lead_priority') }} p
    ) where rn = 1
),

-- the latest capacity-aware operational routing decision for the lead
latest_routing as (
    select lead_id, decision_id as routing_decision_id, as_of_timestamp as routing_decided_at,
           recommendation_status, recommended_rm_id, queue_code, queue_name, queue_reason, queue_owner_role,
           queue_next_action, queue_escalation_path, eligible_candidate_count, candidates_rejected_for_capacity,
           simulated_post_assignment_utilization, remaining_capacity_points_after, territory_eligibility_result,
           specialist_eligibility_result, language_eligibility_result, specialist_requirement_level
    from (
        select r.*, row_number() over (partition by r.lead_id
                                       order by r.as_of_timestamp desc, r.decision_id desc) as rn
        from {{ ref('mart_routing_recommendation_operational') }} r
    ) where rn = 1
),

-- opportunity outcome is already one row per lead, but it is aggregated defensively
opportunity as (
    select lead_id,
           count(*)                                                          as opportunity_count,
           max(case when opportunity_status = 'won' then 1 else 0 end) = 1   as opportunity_won,
           min(opened_at)                                                    as opportunity_opened_at
    from {{ ref('fct_opportunity_outcome') }}
    group by 1
),

-- the current owner's latest capacity position, for the action queue's capacity status
owner_capacity as (
    select rm_id, utilization_ratio, capacity_state, available_capacity_points, weighted_workload_points
    from (
        select s.*, row_number() over (partition by s.rm_id order by s.snapshot_as_of_utc desc) as rn
        from {{ ref('fct_rm_capacity_snapshot') }} s
        cross join params pm
        where s.snapshot_date <= pm.cutoff_date
    ) where rn = 1
)

select
    l.lead_id,
    l.prospect_id,
    l.captured_at,
    cast(l.captured_at as date)                                              as captured_date,
    date_trunc('month', l.captured_at)::date                                 as capture_month,

    -- geography and market context
    coalesce(l.cma_code_sgc2021, pm.unresolved_cma_label)                    as cma_key,
    l.cma_code_sgc2021,
    cma.cma_label_sgc2021,
    cma.tier                                                                 as cma_evidence_tier,
    l.province_code,
    l.territory_id,
    l.geography_status,
    (l.cma_code_sgc2021 is null)                                             as cma_unresolved,

    -- channel, segment, industry
    l.first_channel_code                                                     as channel_code,
    ch.channel_name,
    coalesce(p.sme_segment, 'unknown')                                       as sme_segment,
    p.employee_band,
    coalesce(p.industry_code, pm.unclassified_industry_label)                as industry_grouping,
    p.industry_label,
    (p.industry_code is null or p.industry_code = pm.unclassified_industry_label) as industry_unclassified,

    -- lifecycle state at the cutoff
    l.current_lead_status,
    l.is_terminal,
    l.disposition_code,
    l.status_changed_at,
    l.first_assigned_at,
    l.first_contacted_at,
    l.qualified_at,
    l.converted_at,
    l.current_owner_rm_id,
    l.current_owner_queue_code,
    (l.duplicate_group_id is not null)                                       as is_duplicate_flagged,
    l.duplicate_group_id,

    -- funnel membership, each a clean boolean at lead grain
    true                                                                     as is_captured,
    (l.current_lead_status not in ('INVALID', 'MERGED'))                     as is_valid_lead,
    (l.current_lead_status = 'MERGED' or l.duplicate_group_id is not null)   as is_duplicate_lead,
    (l.first_assigned_at is not null)                                        as is_assigned,
    (l.first_contacted_at is not null)                                       as is_contacted,
    (l.qualified_at is not null)                                             as is_qualified,
    (l.converted_at is not null)                                             as is_converted,
    (l.is_terminal and l.current_lead_status in ('CLOSED_LOST', 'WITHDRAWN', 'INVALID')) as is_closed_or_disqualified,
    (not l.is_terminal)                                                      as is_active,
    (not l.is_terminal
     and datediff('day', l.captured_at, pm.cutoff_date) > pm.ageing_lead_days) as is_ageing,

    -- maturity: a cohort is only judged on conversion once it has had time to convert
    (datediff('day', cast(l.captured_at as date), pm.cutoff_date) >= pm.conversion_maturity_days)
                                                                             as is_conversion_mature,
    (datediff('day', cast(l.captured_at as date), pm.cutoff_date) >= pm.qualification_maturity_days)
                                                                             as is_qualification_mature,
    datediff('day', cast(l.captured_at as date), pm.cutoff_date)             as days_since_capture,

    -- durations, null when the step never happened rather than treated as an infinite delay
    case when l.first_assigned_at is not null
         then datediff('minute', l.captured_at, l.first_assigned_at) / 60.0 end   as hours_to_assignment,
    case when l.first_contacted_at is not null and l.first_assigned_at is not null
         then datediff('minute', l.first_assigned_at, l.first_contacted_at) / 60.0 end as hours_to_first_contact,

    -- assignment history, pre-aggregated
    coalesce(a.assignment_count, 0)                                          as assignment_count,
    coalesce(a.distinct_rms_assigned, 0)                                     as distinct_rms_assigned,
    (coalesce(a.distinct_rms_assigned, 0) > 1)                               as is_reassigned,

    -- first-contact SLA, pre-aggregated to lead grain
    coalesce(fc.first_contact_clocks, 0)                                     as first_contact_clocks,
    (fc.lead_id is not null)                                                 as first_contact_sla_eligible,
    coalesce(fc.first_contact_sla_breached, false)                           as first_contact_sla_breached,
    coalesce(fc.first_contact_sla_open, false)                               as first_contact_sla_open,
    fc.first_contact_sla_target_at,
    fc.first_contact_sla_id,
    fc.first_contact_sla_role,
    -- a clock counts toward compliance only once it is settled: stopped, or past its target
    (fc.lead_id is not null
     and (not coalesce(fc.first_contact_sla_open, false) or fc.first_contact_sla_target_at <= pm.cutoff_date))
                                                                             as first_contact_sla_settled,
    coalesce(ob.open_breached_clocks, 0)                                     as open_breached_clocks,
    case when fc.first_contact_sla_target_at is not null
         then datediff('hour', pm.cutoff_date, fc.first_contact_sla_target_at) end as hours_to_sla_target,

    -- latest priority decision
    lp.recommended_priority_score                                            as priority_score,
    lp.recommended_priority_band                                             as priority_tier,
    lp.recommended_priority_band_name                                        as priority_tier_name,
    lp.priority_eligibility,
    lp.priority_decided_at,

    -- latest operational routing decision
    lr.routing_decision_id,
    lr.routing_decided_at,
    lr.recommendation_status                                                 as routing_status,
    lr.recommended_rm_id,
    lr.queue_code,
    lr.queue_name,
    lr.queue_reason,
    lr.queue_owner_role,
    lr.queue_next_action,
    lr.queue_escalation_path,
    lr.eligible_candidate_count,
    lr.candidates_rejected_for_capacity,
    lr.simulated_post_assignment_utilization,
    lr.territory_eligibility_result,
    lr.specialist_eligibility_result,
    lr.language_eligibility_result,
    lr.specialist_requirement_level,

    -- current owner capacity position
    oc.utilization_ratio                                                     as owner_utilization_ratio,
    oc.capacity_state                                                        as owner_capacity_state,

    -- outcome, descriptive only
    coalesce(o.opportunity_count, 0)                                         as opportunity_count,
    coalesce(o.opportunity_won, false)                                       as opportunity_won,

    -- data-quality exceptions that block or delay routing
    (l.geography_status in ('conflict', 'missing') or l.territory_id is null) as dq_geography_exception,
    (p.industry_code is null or p.industry_code = pm.unclassified_industry_label) as dq_industry_exception,
    (l.current_lead_status = 'VALIDATION_PENDING')                           as dq_validation_pending,
    (l.current_lead_status = 'DUPLICATE_REVIEW')                             as dq_duplicate_review,

    pm.cutoff_date                                                           as reporting_cutoff_date
from lead_base l
cross join params pm
left join {{ ref('dim_business_prospect') }} p   on p.prospect_id = l.prospect_id
left join {{ ref('dim_cma') }} cma                on cma.cma_code_sgc2021 = l.cma_code_sgc2021
left join {{ ref('dim_acquisition_channel') }} ch on ch.channel_code = l.first_channel_code
left join first_contact_sla fc                    on fc.lead_id = l.lead_id
left join open_breaches ob                        on ob.lead_id = l.lead_id
left join assignments a                           on a.lead_id = l.lead_id
left join latest_priority lp                      on lp.lead_id = l.lead_id
left join latest_routing lr                       on lr.lead_id = l.lead_id
left join opportunity o                           on o.lead_id = l.lead_id
left join owner_capacity oc                       on oc.rm_id = l.current_owner_rm_id
where l.captured_at <= pm.cutoff_date + interval 1 day
