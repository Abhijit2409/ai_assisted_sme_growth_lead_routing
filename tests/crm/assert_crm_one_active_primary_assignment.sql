{{ config(tags=['crm_data']) }}
-- At most one active primary assignment per lead; exactly one active RM owner for leads in active work.
select lead_id from {{ ref('stg_crm__lead_assignment') }} where active_flag and assignment_role = 'primary' group by lead_id having count(*) > 1
union all
select l.lead_id from {{ ref('stg_crm__lead') }} l
where l.current_lead_status in ('ASSIGNED', 'CONTACTED', 'NURTURING', 'QUALIFIED')
  and (select count(*) from {{ ref('stg_crm__lead_assignment') }} a where a.lead_id = l.lead_id and a.active_flag and a.assignment_role = 'primary' and a.rm_id is not null) <> 1
