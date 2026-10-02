{{ config(tags=['crm_data']) }}
-- Each approved scenario is observable in the operational data at least its declared minimum number of times (simulation setting).
with l as (select * from {{ ref('stg_crm__lead') }}),
h as (select * from {{ ref('stg_crm__lead_status_history') }}),
a as (select * from {{ ref('stg_crm__lead_assignment') }}),
d as (select * from {{ ref('stg_crm__routing_decision') }}),
e as (select * from {{ ref('stg_crm__sla_event') }}),
rm as (select distinct lead_id from a where rm_id is not null and assignment_role = 'primary'),
obs as (
    select 'SC-02' sc, count(*) n from l join rm using (lead_id) where first_channel_code = 'CH-BRN'
    union all select 'SC-03', count(*) from l join rm using (lead_id) where first_channel_code = 'CH-MKT'
    union all select 'SC-04', count(*) from l join rm using (lead_id) where first_channel_code = 'CH-PTR'
    union all select 'SC-05', count(*) from l join rm using (lead_id) where first_channel_code = 'CH-RMP'
    union all select 'SC-06', count(distinct lead_id) from h where from_status = 'VALIDATION_PENDING' and to_status = 'READY_FOR_ASSIGNMENT'
    union all select 'SC-07', count(*) from l where current_lead_status = 'INVALID'
    union all select 'SC-08', count(*) from l join {{ ref('stg_crm__duplicate_group') }} g using (duplicate_group_id) where l.current_lead_status = 'MERGED' and g.match_tier <> 'review'
    union all select 'SC-09', count(distinct lead_id) from h where from_status = 'DUPLICATE_REVIEW' and to_status = 'MERGED'
    union all select 'SC-10', count(distinct lead_id) from a where rm_id is null
    union all select 'SC-12', count(distinct lead_id) from a where reassignment_reason = 'rm_unavailable'
    union all select 'SC-13', count(distinct lead_id) from d where territory_match_flag = false
    union all select 'SC-14', count(distinct lead_id) from d where specialist_match_flag = false or routing_outcome = 'queued_specialist'
    union all select 'SC-15', count(distinct lead_id) from d where language_match_flag is not null
    union all select 'SC-16', count(distinct e.lead_id) from e join a using (assignment_id) where e.sla_id = 'SLA-05' and e.sla_outcome like 'breached%' and a.assignment_role = 'primary'
    union all select 'SC-17', count(distinct lead_id) from e where sla_id like 'SLA-06%' and sla_outcome like 'breached%'
    union all select 'SC-18', count(distinct lead_id) from h where transition_id = 'T-20' and disposition_code = 'DSP-NO-RESPONSE'
    union all select 'SC-19', count(distinct lead_id) from e where sla_id = 'SLA-08A' and sla_outcome like 'breached%'
    union all select 'SC-20', count(distinct lead_id) from a where reassignment_reason in ('acknowledgement_breach', 'first_contact_breach', 'inactivity')
    union all select 'SC-21', count(distinct lead_id) from a where manual_override_flag
    union all select 'SC-22', count(distinct lead_id) from h where from_status = 'NURTURING' and to_status = 'CONTACTED'
    union all select 'SC-23', count(*) from {{ ref('stg_crm__opportunity_outcome') }}
    union all select 'SC-24', count(distinct lead_id) from h where to_status = 'CONVERTED'
    union all select 'SC-25', count(distinct lead_id) from h where from_status = 'CONTACTED' and to_status = 'CLOSED_LOST'
    union all select 'SC-26', count(distinct lead_id) from h where from_status = 'ASSIGNED' and to_status = 'WITHDRAWN'
    union all select 'SC-27', count(*) from l join rm using (lead_id) where geography_status = 'resolved_province_remainder'
    union all select 'SC-28', count(*) from l join rm using (lead_id) where market_evidence_flag = 'scale_only_limited_evidence'
    union all select 'SC-29', count(*) from l join rm using (lead_id) where market_evidence_flag = 'no_market_context'
    union all select 'SC-31', count(*) from (select lead_id from a where rm_id is not null and assignment_role = 'primary' group by 1 having count(*) >= 3)
    union all select 'SC-32', count(distinct t.lead_id) from {{ ref('stg_crm__lead_source_touch') }} t join l on l.lead_id = t.lead_id
              where t.original_lead_id <> t.lead_id and t.channel_code <> l.first_channel_code
    union all select 'SC-33', count(distinct lead_id) from h where transition_id in ('T-28', 'T-29')
)
select o.sc, o.n, try_cast(p.value as integer) as minimum
from obs o join {{ source('crm_design', 'synthetic_generation_parameters') }} p on p.parameter_name = 'min_count_' || o.sc
where o.n < try_cast(p.value as integer)
