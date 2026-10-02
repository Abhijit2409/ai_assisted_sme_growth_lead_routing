{{ config(tags=['crm_data']) }}
-- Terminal leads carry an applicable disposition, have no active assignment and no open SLA clock (except escalations); non-terminal leads carry no disposition.
with s as (select status_code, is_terminal = 'true' as terminal from {{ source('crm_design', 'lead_status_catalog') }})
select l.lead_id from {{ ref('stg_crm__lead') }} l join s on s.status_code = l.current_lead_status
left join {{ source('crm_design', 'lead_disposition_catalog') }} d on d.disposition_code = l.disposition_code
where s.terminal <> (l.disposition_code is not null) or (l.disposition_code is not null and d.applies_to_status <> l.current_lead_status)
   or (s.terminal and exists (select 1 from {{ ref('stg_crm__lead_assignment') }} a where a.lead_id = l.lead_id and a.active_flag))
   or (s.terminal and exists (select 1 from {{ ref('stg_crm__sla_event') }} e where e.lead_id = l.lead_id and e.stopped_at is null and e.sla_id <> 'SLA-10'))
