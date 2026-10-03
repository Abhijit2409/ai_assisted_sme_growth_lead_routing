-- Where is acquisition constrained by capacity? Each relationship manager's acquisition activity, service
-- performance and capacity position, month by month.
--
-- Grain: relationship manager x reporting month. Primary key: (rm_id, reporting_month).
-- The spine is every active RM crossed with every month of the declared operating period in which that RM was
-- employed, so an RM who received no work still appears as a zero row rather than disappearing.
--
-- Source: dim_relationship_manager, fct_lead_assignment (the month a lead was assigned to this RM),
-- int_acq__lead_spine for lead-level outcomes, fct_rm_capacity_snapshot for the capacity position, and
-- mart_routing_recommendation_operational for what the capacity-aware policy recommended.
--
-- Direction of causality: the historical outcome columns are DESCRIPTIVE. Nothing in this mart feeds back into the
-- routing or priority rules, and no rule was tuned using any value here.
--
-- Limitations: entirely synthetic. Capacity is a declared model (CAP-01 to CAP-10), not a measured workload, and
-- an RM's assignments are attributed to the month of assignment while conversion may happen months later.
with params as (
    select max(case when parameter = 'reporting_cutoff_date'  then cast(value as date) end) as cutoff_date,
           max(case when parameter = 'reporting_period_start' then cast(value as date) end) as period_start
    from {{ ref('acquisition_reporting_parameters') }}
),

thresholds as (
    select max(case when parameter = 'capacity_warning_ratio'    then cast(value as double) end) as warning_ratio,
           max(case when parameter = 'capacity_hard_limit_ratio' then cast(value as double) end) as hard_limit
    from {{ ref('routing_evaluation_thresholds') }}
),

months as (
    select cast(m as date) as reporting_month
    from params p, unnest(generate_series(p.period_start, p.cutoff_date, interval 1 month)) as t(m)
),

-- the RM x month spine: only months in which the RM was actually employed
rm_months as (
    select r.rm_id, m.reporting_month
    from {{ ref('dim_relationship_manager') }} r
    cross join months m
    where r.employment_start_date <= (m.reporting_month + interval 1 month - interval 1 day)
      and (r.employment_end_date is null or r.employment_end_date > m.reporting_month)
),

-- leads assigned to this RM in this month, joined to the lead spine for outcomes (one row per lead by construction)
assigned as (
    select
        a.rm_id,
        date_trunc('month', a.assigned_at)::date                         as reporting_month,
        count(distinct a.lead_id)                                        as assigned_leads,
        count(distinct a.lead_id) filter (where s.is_active)             as active_leads,
        count(distinct a.lead_id) filter (where s.priority_tier = 'P1')  as p1_assignments,
        count(distinct a.lead_id) filter (where s.priority_tier = 'P2')  as p2_assignments,
        count(distinct a.lead_id) filter (where s.priority_tier = 'P3')  as p3_assignments,
        count(distinct a.lead_id) filter (where s.is_contacted)          as contacted_leads,
        count(distinct a.lead_id) filter (where s.is_qualified)          as qualified_leads,
        count(distinct a.lead_id) filter (where s.is_converted)          as converted_leads,
        count(distinct a.lead_id) filter (where s.is_reassigned)         as reassigned_leads,
        count(distinct a.lead_id) filter (where s.first_contact_sla_settled) as first_contact_sla_settled,
        count(distinct a.lead_id) filter (where s.first_contact_sla_settled
                                            and not s.first_contact_sla_breached) as first_contact_sla_met
    from {{ ref('fct_lead_assignment') }} a
    join {{ ref('int_acq__lead_spine') }} s on s.lead_id = a.lead_id
    cross join params p
    where a.assignment_role = 'primary' and a.rm_id is not null and a.assigned_at <= p.cutoff_date + interval 1 day
    group by 1, 2
),

-- the RM's capacity position at the end of each month: the last snapshot on or before the month end
capacity as (
    select rm_id, reporting_month, available_capacity_points, weighted_workload_points,
           utilization_ratio, capacity_state, availability_status
    from (
        select s.rm_id,
               date_trunc('month', s.snapshot_date)::date as reporting_month,
               s.available_capacity_points, s.weighted_workload_points, s.utilization_ratio,
               s.capacity_state, s.availability_status,
               row_number() over (partition by s.rm_id, date_trunc('month', s.snapshot_date)
                                  order by s.snapshot_as_of_utc desc) as rn
        from {{ ref('fct_rm_capacity_snapshot') }} s
        cross join params p
        where s.snapshot_date <= p.cutoff_date
    ) where rn = 1
),

-- what the capacity-aware operational policy recommended in this month
routing as (
    select
        recommended_rm_id                                                as rm_id,
        date_trunc('month', as_of_timestamp)::date                       as reporting_month,
        count(*)                                                         as operational_recommendations,
        count(*) filter (where candidates_rejected_for_capacity > 0)     as recommendations_after_capacity_skip,
        max(simulated_post_assignment_utilization)                       as max_simulated_utilization,
        min(remaining_capacity_points_after)                             as min_remaining_capacity_points
    from {{ ref('mart_routing_recommendation_operational') }}
    where recommendation_status = 'assigned' and recommended_rm_id is not null
    group by 1, 2
),

-- Routing exceptions attributed to the RM home territory. A queued decision has no recommended RM by definition,
-- so it cannot be attributed to an individual; attributing it to the territory the lead sits in is the closest
-- defensible attribution and is labelled as such.
exceptions as (
    select
        q.territory_id,
        date_trunc('month', q.as_of_timestamp)::date                     as reporting_month,
        count(*)                                                         as routing_exceptions
    from {{ ref('mart_routing_recommendation_operational') }} q
    where q.recommendation_status = 'queued' and q.territory_id is not null
    group by 1, 2
)

select
    s.rm_id,
    s.reporting_month,
    r.rm_display_name,
    r.rm_code,
    r.rm_segment,
    r.rm_role,
    r.home_territory_id,
    t.territory_code                                                     as home_territory_code,
    t.sales_region                                                       as home_sales_region,
    t.cma_code_sgc2021                                                   as home_cma_code_sgc2021,
    r.primary_specialization_industry_code,
    (r.primary_specialization_industry_code is not null)                 as is_specialist,
    r.base_capacity_points,

    -- capacity position at month end
    cap.available_capacity_points                                        as capacity_limit_points,
    cap.weighted_workload_points,
    cap.utilization_ratio,
    cap.capacity_state,
    cap.availability_status,
    case when cap.available_capacity_points is not null
         then cap.available_capacity_points - cap.weighted_workload_points end as remaining_capacity_points,
    (cap.utilization_ratio >= th.warning_ratio)                          as at_or_above_capacity_warning,
    (cap.utilization_ratio >= th.hard_limit)                             as at_or_above_hard_limit,
    (cap.rm_id is null)                                                  as no_capacity_evidence,

    -- acquisition activity (descriptive history)
    coalesce(a.assigned_leads, 0)                                        as assigned_leads,
    coalesce(a.active_leads, 0)                                          as active_leads,
    coalesce(a.p1_assignments, 0)                                        as p1_assignments,
    coalesce(a.p2_assignments, 0)                                        as p2_assignments,
    coalesce(a.p3_assignments, 0)                                        as p3_assignments,
    coalesce(a.contacted_leads, 0)                                       as contacted_leads,
    coalesce(a.qualified_leads, 0)                                       as qualified_leads,
    coalesce(a.converted_leads, 0)                                       as converted_leads,
    coalesce(a.reassigned_leads, 0)                                      as reassigned_leads,

    -- first-contact SLA performance
    coalesce(a.first_contact_sla_settled, 0)                             as first_contact_sla_settled,
    coalesce(a.first_contact_sla_met, 0)                                 as first_contact_sla_met,
    case when coalesce(a.first_contact_sla_settled, 0) > 0
         then a.first_contact_sla_met * 1.0 / a.first_contact_sla_settled end as first_contact_sla_compliance_rate,

    -- what the capacity-aware policy would have done
    coalesce(rt.operational_recommendations, 0)                          as operational_recommendations,
    coalesce(rt.recommendations_after_capacity_skip, 0)                  as recommendations_after_capacity_skip,
    rt.max_simulated_utilization,
    rt.min_remaining_capacity_points,
    coalesce(ex.routing_exceptions, 0)                                   as routing_exceptions_in_home_territory,

    p.cutoff_date                                                        as reporting_cutoff_date
from rm_months s
cross join params p
cross join thresholds th
join {{ ref('dim_relationship_manager') }} r on r.rm_id = s.rm_id
left join {{ ref('dim_crm_territory') }} t   on t.territory_id = r.home_territory_id
left join assigned a   on a.rm_id = s.rm_id and a.reporting_month = s.reporting_month
left join capacity cap on cap.rm_id = s.rm_id and cap.reporting_month = s.reporting_month
left join routing rt   on rt.rm_id = s.rm_id and rt.reporting_month = s.reporting_month
left join exceptions ex on ex.territory_id = r.home_territory_id and ex.reporting_month = s.reporting_month
