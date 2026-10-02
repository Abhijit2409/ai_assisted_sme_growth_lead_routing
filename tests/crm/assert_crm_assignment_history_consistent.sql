{{ config(tags=['crm_data']) }}
-- Assignment status, active flag and timestamps agree; queue rows have a queue and no RM; reassignments keep the prior RM and follow the prior assignment.
select a.assignment_id from {{ ref('stg_crm__lead_assignment') }} a
left join {{ ref('stg_crm__lead_assignment') }} p on p.assignment_id = a.prior_assignment_id
where a.active_flag <> (a.assignment_status in ('QUEUED', 'ASSIGNED', 'ACKNOWLEDGED', 'REASSIGNMENT_PENDING'))
   or a.active_flag = (a.ended_at is not null)
   or (a.rm_id is null) <> (a.queue_code is not null)
   or (a.assignment_reason = 'reassignment' and a.prior_assignment_id is null)
   or (a.prior_assignment_id is not null and (p.lead_id <> a.lead_id or p.rm_id is distinct from a.prior_rm_id or p.active_flag
       or a.assigned_at < p.assigned_at))
   or a.acknowledged_at < a.assigned_at or a.ended_at < a.assigned_at
