-- Every published measure is non-negative, every rate sits in [0, 1], and no numerator exceeds its denominator.
with funnel as (select * from {{ ref('mart_acquisition_funnel_monthly') }}),
     focus  as (select * from {{ ref('mart_acquisition_market_focus') }}),
     rm     as (select * from {{ ref('mart_rm_acquisition_performance_monthly') }}),
     kpi    as (select * from {{ ref('mart_acquisition_kpi_snapshot') }})

select 'funnel: a count measure is negative' as failure, count(*)::varchar as detail
from funnel
where least(captured_leads, valid_leads, duplicate_leads, assigned_leads, contacted_leads, qualified_leads,
            converted_leads, closed_or_disqualified_leads, active_backlog, ageing_leads, reassigned_leads,
            valid_leads_mature, converted_leads_mature, valid_leads_immature, contacted_leads_mature,
            qualified_leads_mature, first_contact_sla_eligible, first_contact_sla_settled,
            first_contact_sla_breached, first_contact_sla_met) < 0
having count(*) > 0

union all
select 'funnel: a rate is outside [0, 1]', count(*)::varchar
from funnel
where assignment_rate                   not between 0 and 1
   or contact_rate                      not between 0 and 1
   or qualification_rate                not between 0 and 1
   or mature_cohort_conversion_rate     not between 0 and 1
   or duplicate_rate                    not between 0 and 1
   or first_contact_sla_compliance_rate not between 0 and 1
   or reassignment_rate                 not between 0 and 1
having count(*) > 0

union all
select 'funnel: a numerator exceeds its denominator', count(*)::varchar
from funnel
where assigned_leads             > valid_leads
   or contacted_leads            > assigned_leads
   or qualified_leads_mature     > contacted_leads_mature
   or converted_leads_mature     > valid_leads_mature
   or duplicate_leads            > captured_leads
   or reassigned_leads           > assigned_leads
   or first_contact_sla_breached > first_contact_sla_settled
   or first_contact_sla_met      > first_contact_sla_settled
   or first_contact_sla_settled  > first_contact_sla_eligible
   or valid_leads                > captured_leads
   or active_backlog             > captured_leads
having count(*) > 0

union all
select 'funnel: a median duration is negative', count(*)::varchar
from funnel
where median_hours_to_assignment < 0 or median_hours_to_first_contact < 0
having count(*) > 0

union all
select 'market focus: a count measure is negative', count(*)::varchar
from focus
where least(prospect_count, lead_count, active_lead_backlog, qualified_leads, converted_leads, p1_leads, p2_leads,
            p3_leads, unassigned_active_leads, queued_active_leads, open_sla_breaches, ageing_leads,
            data_quality_exceptions) < 0
having count(*) > 0

union all
select 'market focus: a rate is outside [0, 1]', count(*)::varchar
from focus
where mature_cohort_conversion_rate not between 0 and 1
   or (opportunity_score is not null and opportunity_score not between 0 and 100)
having count(*) > 0

union all
select 'market focus: a sub-count exceeds the lead count', count(*)::varchar
from focus
where active_lead_backlog > lead_count or qualified_leads > lead_count or converted_leads > lead_count
   or p1_leads + p2_leads + p3_leads > lead_count or mature_converted_leads > mature_valid_leads
having count(*) > 0

union all
select 'rm monthly: a count measure is negative', count(*)::varchar
from rm
where least(assigned_leads, active_leads, p1_assignments, p2_assignments, p3_assignments, contacted_leads,
            qualified_leads, converted_leads, reassigned_leads, first_contact_sla_settled, first_contact_sla_met,
            operational_recommendations, recommendations_after_capacity_skip, routing_exceptions_in_home_territory) < 0
having count(*) > 0

union all
select 'rm monthly: a numerator exceeds its denominator', count(*)::varchar
from rm
where contacted_leads > assigned_leads or qualified_leads > assigned_leads or converted_leads > assigned_leads
   or first_contact_sla_met > first_contact_sla_settled
   or p1_assignments + p2_assignments + p3_assignments > assigned_leads
having count(*) > 0

union all
select 'rm monthly: the SLA compliance rate is outside [0, 1]', count(*)::varchar
from rm where first_contact_sla_compliance_rate not between 0 and 1
having count(*) > 0

union all
select 'kpi snapshot: a rate is outside [0, 1]', count(*)::varchar
from kpi
where assignment_rate                    not between 0 and 1
   or contact_rate                       not between 0 and 1
   or qualification_rate                 not between 0 and 1
   or mature_cohort_conversion_rate      not between 0 and 1
   or duplicate_rate                     not between 0 and 1
   or sla_breach_rate                    not between 0 and 1
   or operational_routing_assignment_rate not between 0 and 1
   or routing_top_decile_share           not between 0 and 1
   or routing_gini                       not between 0 and 1
having count(*) > 0

union all
select 'kpi snapshot: a numerator exceeds its denominator', count(*)::varchar
from kpi
where assignment_rate_numerator  > assignment_rate_denominator
   or contact_rate_numerator     > contact_rate_denominator
   or qualification_rate_numerator > qualification_rate_denominator
   or conversion_rate_numerator  > conversion_rate_denominator
   or duplicate_rate_numerator   > duplicate_rate_denominator
   or sla_breach_count           > sla_settled_count
   or operational_assignment_rate_numerator > operational_assignment_rate_denominator
   or routing_top_decile_numerator > routing_top_decile_denominator
having count(*) > 0

union all
select 'kpi snapshot: a count measure is negative', count(*)::varchar
from kpi
where least(high_opportunity_cma_count, total_leads, active_leads, actionable_leads, p1_leads_requiring_action,
            sla_breach_count, unassigned_or_queued_leads, rms_above_capacity_warning,
            operational_hard_cap_breaches, data_quality_exception_count, rms_with_no_recommendations) < 0
having count(*) > 0
