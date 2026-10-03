-- The executive view: one row holding every headline acquisition KPI at the reporting cutoff.
--
-- Grain: one row per reporting cutoff date. Primary key: reporting_cutoff_date.
--
-- How this is built, and why it is built this way. Each KPI is computed in its own CTE, at its own correct grain,
-- and reduced to a single value there. Only then are the single-value results combined with a cross join. No fact
-- table is ever joined to another fact table at row level, so no measure can be inflated by another table's rows.
-- Lead-level KPIs come from int_acq__lead_spine (one row per lead), RM-level KPIs from the capacity snapshots
-- (one row per RM) and routing KPIs from the operational recommendation (one row per decision).
--
-- Every KPI in this mart is defined in the machine-readable catalog seed acquisition_kpi_catalog, which carries its
-- business definition, formula, numerator, denominator, grain, filters, maturity rule, null handling,
-- interpretation, limitation and stakeholder.
--
-- Limitations: entirely synthetic CRM data over hypothetical rules. No value here is evidence about a real bank,
-- and no rate is used to tune any priority or routing rule.
with params as (
    select max(case when parameter = 'reporting_cutoff_date' then cast(value as date) end) as cutoff_date
    from {{ ref('acquisition_reporting_parameters') }}
),

thresholds as (
    select max(case when parameter = 'capacity_warning_ratio'    then cast(value as double) end) as warning_ratio,
           max(case when parameter = 'capacity_hard_limit_ratio' then cast(value as double) end) as hard_limit
    from {{ ref('routing_evaluation_thresholds') }}
),

-- ---------------------------------------------------------------- market KPIs, at CMA grain
market as (
    select count(*) filter (where is_high_opportunity)            as high_opportunity_cma_count,
           count(*) filter (where is_cma_scope)                   as cma_count,
           count(*) filter (where missing_opportunity_score)      as cmas_without_opportunity_score
    from {{ ref('mart_acquisition_market_focus') }}
),

-- ---------------------------------------------------------------- lead KPIs, at lead grain
leads as (
    select
        count(*)                                                                  as total_leads,
        count(*) filter (where is_active)                                         as active_leads,
        count(*) filter (where is_valid_lead)                                     as valid_leads,
        count(*) filter (where is_assigned)                                       as assigned_leads,
        count(*) filter (where is_contacted)                                      as contacted_leads,
        count(*) filter (where is_qualification_mature and is_contacted)          as contacted_leads_mature,
        count(*) filter (where is_qualification_mature and is_qualified)          as qualified_leads_mature,
        count(*) filter (where is_conversion_mature and is_valid_lead)            as valid_leads_mature,
        count(*) filter (where is_conversion_mature and is_converted)             as converted_leads_mature,
        count(*) filter (where is_duplicate_lead)                                 as duplicate_leads,
        count(*) filter (where first_contact_sla_settled)                         as first_contact_sla_settled,
        count(*) filter (where first_contact_sla_settled and first_contact_sla_breached) as first_contact_sla_breached,
        count(*) filter (where is_active and (current_owner_rm_id is null or routing_status = 'queued'))
                                                                                  as unassigned_or_queued_leads,
        count(*) filter (where dq_geography_exception or dq_industry_exception
                            or dq_validation_pending or dq_duplicate_review)      as data_quality_exception_count
    from {{ ref('int_acq__lead_spine') }}
),

-- ---------------------------------------------------------------- action KPIs, at actionable-lead grain
actions as (
    select count(*)                                               as actionable_leads,
           count(*) filter (where priority_tier = 'P1')           as p1_leads_requiring_action,
           count(*) filter (where requires_escalation)            as leads_requiring_escalation
    from {{ ref('mart_acquisition_action_queue') }}
),

-- ---------------------------------------------------------------- capacity KPIs, at RM grain
latest_capacity as (
    select rm_id, utilization_ratio
    from (
        select s.*, row_number() over (partition by s.rm_id order by s.snapshot_as_of_utc desc) as rn
        from {{ ref('fct_rm_capacity_snapshot') }} s
        cross join params p
        where s.snapshot_date <= p.cutoff_date
    ) where rn = 1
),

capacity as (
    select count(*) filter (where lc.utilization_ratio >= th.warning_ratio) as rms_above_capacity_warning,
           count(*) filter (where lc.utilization_ratio >= th.hard_limit)    as rms_at_or_above_hard_limit,
           count(*)                                                         as rms_with_capacity_evidence
    from {{ ref('dim_relationship_manager') }} r
    left join latest_capacity lc on lc.rm_id = r.rm_id
    cross join thresholds th
    where r.active_flag
),

-- ---------------------------------------------------------------- routing KPIs, at routing-decision grain
routing as (
    select count(*)                                                         as routing_decisions,
           count(*) filter (where recommendation_status = 'assigned')       as routing_assigned,
           count(*) filter (where recommendation_status = 'assigned'
                              and simulated_post_assignment_utilization >= (select hard_limit from thresholds))
                                                                            as operational_hard_cap_breaches
    from {{ ref('mart_routing_recommendation_operational') }}
),

-- routing concentration, at RM grain, reindexed over every RM so an RM with no work counts as a zero
rm_load as (
    select r.rm_id, count(o.decision_id) as n
    from {{ ref('dim_relationship_manager') }} r
    left join {{ ref('mart_routing_recommendation_operational') }} o
           on o.recommended_rm_id = r.rm_id and o.recommendation_status = 'assigned'
    group by 1
),

rm_load_ranked as (
    select l.*,
           ntile(10)    over (order by l.n desc, l.rm_id)  as load_decile,
           row_number() over (order by l.n asc,  l.rm_id)  as asc_rank,
           count(*)     over ()                            as rm_count,
           sum(l.n)     over ()                            as total_assignments
    from rm_load l
),

concentration as (
    select
        max(rm_count)                                                       as rm_count,
        sum(case when n = 0 then 1 else 0 end)                              as rms_with_no_recommendations,
        max(n)                                                              as max_recommendations_to_one_rm,
        sum(case when load_decile = 1 then n else 0 end)                    as top_decile_recommendations,
        max(total_assignments)                                              as total_recommendations,
        case when max(total_assignments) > 0
             then sum((2 * asc_rank - rm_count - 1) * n)
                  / (max(rm_count)::double * max(total_assignments)) end    as routing_gini
    from rm_load_ranked
)

select
    p.cutoff_date                                                           as reporting_cutoff_date,

    -- market focus
    m.high_opportunity_cma_count,
    m.cma_count,
    m.cmas_without_opportunity_score,

    -- volume
    l.total_leads,
    l.active_leads,
    a.actionable_leads,
    a.p1_leads_requiring_action,
    a.leads_requiring_escalation,

    -- funnel rates, each with its numerator and denominator published beside it
    l.assigned_leads                                                        as assignment_rate_numerator,
    l.valid_leads                                                           as assignment_rate_denominator,
    case when l.valid_leads > 0 then l.assigned_leads * 1.0 / l.valid_leads end          as assignment_rate,
    l.contacted_leads                                                       as contact_rate_numerator,
    l.assigned_leads                                                        as contact_rate_denominator,
    case when l.assigned_leads > 0 then l.contacted_leads * 1.0 / l.assigned_leads end   as contact_rate,
    l.qualified_leads_mature                                                as qualification_rate_numerator,
    l.contacted_leads_mature                                                as qualification_rate_denominator,
    case when l.contacted_leads_mature > 0
         then l.qualified_leads_mature * 1.0 / l.contacted_leads_mature end                as qualification_rate,
    l.converted_leads_mature                                                as conversion_rate_numerator,
    l.valid_leads_mature                                                    as conversion_rate_denominator,
    case when l.valid_leads_mature > 0
         then l.converted_leads_mature * 1.0 / l.valid_leads_mature end                    as mature_cohort_conversion_rate,
    l.duplicate_leads                                                       as duplicate_rate_numerator,
    l.total_leads                                                           as duplicate_rate_denominator,
    case when l.total_leads > 0 then l.duplicate_leads * 1.0 / l.total_leads end          as duplicate_rate,

    -- service health
    l.first_contact_sla_breached                                            as sla_breach_count,
    l.first_contact_sla_settled                                             as sla_settled_count,
    case when l.first_contact_sla_settled > 0
         then l.first_contact_sla_breached * 1.0 / l.first_contact_sla_settled end         as sla_breach_rate,
    l.unassigned_or_queued_leads,

    -- capacity
    c.rms_above_capacity_warning,
    c.rms_at_or_above_hard_limit,
    c.rms_with_capacity_evidence,

    -- operational routing
    rt.routing_assigned                                                     as operational_assignment_rate_numerator,
    rt.routing_decisions                                                    as operational_assignment_rate_denominator,
    case when rt.routing_decisions > 0
         then rt.routing_assigned * 1.0 / rt.routing_decisions end                         as operational_routing_assignment_rate,
    rt.operational_hard_cap_breaches,

    -- routing concentration: an operational risk, reported honestly
    con.top_decile_recommendations                                          as routing_top_decile_numerator,
    con.total_recommendations                                               as routing_top_decile_denominator,
    case when con.total_recommendations > 0
         then con.top_decile_recommendations * 1.0 / con.total_recommendations end         as routing_top_decile_share,
    con.routing_gini,
    con.max_recommendations_to_one_rm,
    con.rms_with_no_recommendations,
    con.rm_count                                                            as rm_population,

    -- data quality
    l.data_quality_exception_count
from params p
cross join market m
cross join leads l
cross join actions a
cross join capacity c
cross join routing rt
cross join concentration con
