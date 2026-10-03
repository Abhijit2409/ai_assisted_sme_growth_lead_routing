-- Source reconciliation and fan-out control. Every acquisition mart must trace back to exactly the lead population
-- in fct_lead, with no lead counted twice by status, assignment, SLA or routing history.
with spine  as (select * from {{ ref('int_acq__lead_spine') }}),
     funnel as (select * from {{ ref('mart_acquisition_funnel_monthly') }}),
     focus  as (select * from {{ ref('mart_acquisition_market_focus') }}),
     queue  as (select * from {{ ref('mart_acquisition_action_queue') }}),
     kpi    as (select * from {{ ref('mart_acquisition_kpi_snapshot') }}),
     leads  as (select * from {{ ref('fct_lead') }})

-- ---------------------------------------------------------------- the spine is exactly one row per source lead
select 'the lead spine does not match fct_lead one-for-one' as failure,
       ((select count(*) from spine)::varchar || ' vs ' || (select count(*) from leads)::varchar) as detail
from (select 1)
where (select count(*) from spine) <> (select count(*) from leads)
   or (select count(distinct lead_id) from spine) <> (select count(*) from leads)

union all
select 'a lead in the spine is missing from fct_lead', count(*)::varchar
from spine s where not exists (select 1 from leads l where l.lead_id = s.lead_id)
having count(*) > 0

-- ---------------------------------------------------------------- fan-out control: the marts reconcile to the source
union all
select 'the monthly funnel does not reconcile to the source lead count',
       ((select coalesce(sum(captured_leads), 0) from funnel)::varchar || ' vs '
        || (select count(*) from leads)::varchar)
from (select 1)
where (select coalesce(sum(captured_leads), 0) from funnel) <> (select count(*) from leads)

union all
select 'the market focus mart does not reconcile to the source lead count',
       ((select coalesce(sum(lead_count), 0) from focus)::varchar || ' vs '
        || (select count(*) from leads)::varchar)
from (select 1)
where (select coalesce(sum(lead_count), 0) from focus) <> (select count(*) from leads)

union all
select 'the executive snapshot total does not reconcile to the source lead count',
       ((select total_leads from kpi)::varchar || ' vs ' || (select count(*) from leads)::varchar)
from (select 1)
where (select total_leads from kpi) <> (select count(*) from leads)

-- the same lead must never appear in two funnel cells
union all
select 'a lead appears in more than one funnel cell', count(*)::varchar
from (
    select lead_id, count(*) n
    from (select lead_id, capture_month, cma_key, channel_code, sme_segment, industry_grouping from spine)
    group by 1
) where n > 1
having count(*) > 0

-- ---------------------------------------------------------------- priority and routing reconciliation
union all
select 'a spine priority tier does not match the latest published priority decision', count(*)::varchar
from spine s
join (
    select lead_id, recommended_priority_band, row_number() over (partition by lead_id
          order by as_of_timestamp desc, decision_id desc) rn
    from {{ ref('mart_lead_priority') }}
) p on p.lead_id = s.lead_id and p.rn = 1
where s.priority_tier is distinct from p.recommended_priority_band
having count(*) > 0

union all
select 'a spine routing result does not match the latest published operational decision', count(*)::varchar
from spine s
join (
    select lead_id, recommendation_status, recommended_rm_id, queue_code,
           row_number() over (partition by lead_id order by as_of_timestamp desc, decision_id desc) rn
    from {{ ref('mart_routing_recommendation_operational') }}
) r on r.lead_id = s.lead_id and r.rn = 1
where s.routing_status is distinct from r.recommendation_status
   or s.recommended_rm_id is distinct from r.recommended_rm_id
   or s.queue_code is distinct from r.queue_code
having count(*) > 0

-- ---------------------------------------------------------------- executive KPI reconciliation
union all
select 'the executive active-lead count does not match the spine', count(*)::varchar
from kpi k where k.active_leads <> (select count(*) from spine where is_active)
having count(*) > 0

union all
select 'the executive actionable count does not match the action queue', count(*)::varchar
from kpi k where k.actionable_leads <> (select count(*) from queue)
having count(*) > 0

union all
select 'the executive P1 action count does not match the action queue', count(*)::varchar
from kpi k
where k.p1_leads_requiring_action <> (select count(*) from queue where priority_tier = 'P1')
having count(*) > 0

union all
select 'the executive assignment rate does not match its own numerator and denominator', count(*)::varchar
from kpi k
where abs(k.assignment_rate - k.assignment_rate_numerator * 1.0 / nullif(k.assignment_rate_denominator, 0)) > 1e-9
having count(*) > 0

union all
select 'the executive conversion rate does not match the funnel totals', count(*)::varchar
from kpi k
where k.conversion_rate_numerator   <> (select coalesce(sum(converted_leads_mature), 0) from funnel)
   or k.conversion_rate_denominator <> (select coalesce(sum(valid_leads_mature), 0) from funnel)
having count(*) > 0

union all
select 'the executive SLA breach count does not match the funnel totals', count(*)::varchar
from kpi k
where k.sla_breach_count <> (select coalesce(sum(first_contact_sla_breached), 0) from funnel)
having count(*) > 0

union all
select 'the executive routing concentration does not match the operational recommendation', count(*)::varchar
from kpi k
where k.routing_top_decile_denominator <> (select count(*) from {{ ref('mart_routing_recommendation_operational') }}
                                           where recommendation_status = 'assigned')
having count(*) > 0
