-- Where should the bank focus SME acquisition effort? External market opportunity set beside the acquisition
-- activity, backlog, service health and capacity actually present in each market.
--
-- Grain: one row per CMA at the current planning snapshot, plus one explicit row for leads whose geography has not
-- resolved to a CMA, so the mart always reconciles to the full lead population. Primary key: cma_key.
--
-- Source: dim_cma and mart_cma_opportunity_framework (balanced planning scenario) for the external signal;
-- int_acq__lead_spine for every lead-level count, which is already collapsed to one row per lead;
-- fct_rm_capacity_snapshot and dim_crm_territory for capacity.
--
-- As-of rule: a current planning snapshot at the declared reporting cutoff (2026-05-31, assumption A-018). The
-- opportunity score describes the market now and is deliberately NOT applied retrospectively to historical
-- periods; the monthly funnel mart carries no opportunity score for that reason.
--
-- Limitations: the opportunity score is an external public-aggregate signal only. It excludes the bank's market
-- share, branch presence, competition, credit risk and acquisition cost. All CRM counts are synthetic.
with params as (
    select max(case when parameter = 'reporting_cutoff_date' then cast(value as date) end)   as cutoff_date,
           max(case when parameter = 'planning_scenario_id'   then value end)                as scenario_id,
           max(case when parameter = 'high_opportunity_classification' then value end)       as high_opportunity_class,
           max(case when parameter = 'unresolved_cma_label'   then value end)                as unresolved_label,
           max(case when parameter = 'capacity_warning_ratio_proxy' then value end)          as unused
    from {{ ref('acquisition_reporting_parameters') }}
),

warning_ratio as (
    select cast(value as double) as ratio
    from {{ ref('routing_evaluation_thresholds') }} where parameter = 'capacity_warning_ratio'
),

opportunity as (
    select f.cma_code_sgc2021, f.opportunity_score, f.score_rank, f.opportunity_classification,
           f.evidence_tier, f.dominant_pillar,
           f.pillar_scale, f.pillar_momentum, f.pillar_dynamism, f.pillar_commercial_fit, f.pillar_stability
    from {{ ref('mart_cma_opportunity_framework') }} f
    cross join params p
    where f.scenario_id = p.scenario_id
),

-- every lead measure, aggregated once at CMA grain from the lead spine (no fan-out possible)
lead_counts as (
    select
        cma_key,
        count(*)                                                        as lead_count,
        count(distinct prospect_id)                                     as prospect_count,
        count(*) filter (where is_active)                               as active_lead_backlog,
        count(*) filter (where is_qualified)                            as qualified_leads,
        count(*) filter (where is_converted)                            as converted_leads,
        count(*) filter (where is_conversion_mature and is_valid_lead)  as mature_valid_leads,
        count(*) filter (where is_conversion_mature and is_converted)   as mature_converted_leads,
        count(*) filter (where priority_tier = 'P1')                    as p1_leads,
        count(*) filter (where priority_tier = 'P2')                    as p2_leads,
        count(*) filter (where priority_tier = 'P3')                    as p3_leads,
        count(*) filter (where is_active and current_owner_rm_id is null) as unassigned_active_leads,
        count(*) filter (where is_active and routing_status = 'queued') as queued_active_leads,
        count(*) filter (where first_contact_sla_breached and first_contact_sla_open) as open_sla_breaches,
        count(*) filter (where first_contact_sla_breached)              as first_contact_sla_breaches,
        count(*) filter (where is_ageing)                               as ageing_leads,
        count(*) filter (where dq_geography_exception or dq_industry_exception
                            or dq_validation_pending or dq_duplicate_review) as data_quality_exceptions
    from {{ ref('int_acq__lead_spine') }}
    group by 1
),

-- RM capacity attributed to the CMA of the RM's home territory; RMs whose home territory has no CMA are counted
-- separately rather than silently dropped
latest_capacity as (
    select rm_id, available_capacity_points, weighted_workload_points, utilization_ratio, capacity_state
    from (
        select s.*, row_number() over (partition by s.rm_id order by s.snapshot_as_of_utc desc) as rn
        from {{ ref('fct_rm_capacity_snapshot') }} s
        cross join params p
        where s.snapshot_date <= p.cutoff_date
    ) where rn = 1
),

capacity_by_cma as (
    select
        t.cma_code_sgc2021                                              as cma_code_sgc2021,
        count(*)                                                        as rms_home_based_here,
        count(lc.rm_id)                                                 as rms_with_capacity_evidence,
        sum(lc.available_capacity_points - lc.weighted_workload_points) as available_capacity_points,
        sum(lc.available_capacity_points)                               as total_capacity_points,
        count(*) filter (where lc.utilization_ratio >= (select ratio from warning_ratio)) as rms_at_or_above_warning
    from {{ ref('dim_relationship_manager') }} r
    join {{ ref('dim_crm_territory') }} t on t.territory_id = r.home_territory_id
    left join latest_capacity lc on lc.rm_id = r.rm_id
    where r.active_flag and t.cma_code_sgc2021 is not null
    group by 1
),

-- the CMA spine, plus the explicit unresolved bucket so the mart reconciles to every lead
spine as (
    select d.cma_code_sgc2021 as cma_key, d.cma_code_sgc2021, d.cma_label_sgc2021, d.province_code,
           d.tier as evidence_tier, d.evidence_label, true as is_cma_scope
    from {{ ref('dim_cma') }} d
    union all
    select p.unresolved_label, null, 'Geography not resolved to a CMA', null, null,
           'No CMA evidence: the lead geography did not resolve', false
    from params p
)

select
    s.cma_key,
    s.cma_code_sgc2021,
    s.cma_label_sgc2021,
    s.province_code,
    s.is_cma_scope,

    -- external market opportunity (current planning snapshot only)
    o.opportunity_score,
    o.score_rank                                                        as opportunity_rank,
    o.opportunity_classification,
    coalesce(o.evidence_tier, s.evidence_tier)                          as evidence_tier,
    s.evidence_label,
    o.dominant_pillar,
    o.pillar_scale,
    o.pillar_momentum,
    o.pillar_dynamism,
    o.pillar_commercial_fit,
    o.pillar_stability,
    (o.cma_code_sgc2021 is not null)                                    as has_opportunity_score,
    (o.opportunity_classification = p.high_opportunity_class)           as is_high_opportunity,

    -- acquisition activity present in the market
    coalesce(lc.prospect_count, 0)                                      as prospect_count,
    coalesce(lc.lead_count, 0)                                          as lead_count,
    coalesce(lc.active_lead_backlog, 0)                                 as active_lead_backlog,
    coalesce(lc.qualified_leads, 0)                                     as qualified_leads,
    coalesce(lc.converted_leads, 0)                                     as converted_leads,
    coalesce(lc.mature_valid_leads, 0)                                  as mature_valid_leads,
    coalesce(lc.mature_converted_leads, 0)                              as mature_converted_leads,
    case when coalesce(lc.mature_valid_leads, 0) > 0
         then lc.mature_converted_leads * 1.0 / lc.mature_valid_leads end as mature_cohort_conversion_rate,

    -- priority mix
    coalesce(lc.p1_leads, 0)                                            as p1_leads,
    coalesce(lc.p2_leads, 0)                                            as p2_leads,
    coalesce(lc.p3_leads, 0)                                            as p3_leads,

    -- service and routing health
    coalesce(lc.unassigned_active_leads, 0)                             as unassigned_active_leads,
    coalesce(lc.queued_active_leads, 0)                                 as queued_active_leads,
    coalesce(lc.open_sla_breaches, 0)                                   as open_sla_breaches,
    coalesce(lc.first_contact_sla_breaches, 0)                          as first_contact_sla_breaches,
    coalesce(lc.ageing_leads, 0)                                        as ageing_leads,

    -- capacity, attributed by RM home territory
    coalesce(cap.rms_home_based_here, 0)                                as rms_home_based_here,
    coalesce(cap.rms_with_capacity_evidence, 0)                         as rms_with_capacity_evidence,
    cap.available_capacity_points,
    cap.total_capacity_points,
    coalesce(cap.rms_at_or_above_warning, 0)                            as rms_at_or_above_warning,
    (cap.rms_home_based_here is null)                                   as no_capacity_evidence,

    -- missing-evidence flags, so a gap is never read as a zero
    (o.cma_code_sgc2021 is null and s.is_cma_scope)                     as missing_opportunity_score,
    (coalesce(lc.lead_count, 0) = 0)                                    as missing_acquisition_activity,
    coalesce(lc.data_quality_exceptions, 0)                             as data_quality_exceptions,

    p.cutoff_date                                                       as reporting_cutoff_date,
    p.scenario_id                                                       as planning_scenario_id
from spine s
cross join params p
left join opportunity o      on o.cma_code_sgc2021 = s.cma_code_sgc2021
left join lead_counts lc     on lc.cma_key = s.cma_key
left join capacity_by_cma cap on cap.cma_code_sgc2021 = s.cma_code_sgc2021
