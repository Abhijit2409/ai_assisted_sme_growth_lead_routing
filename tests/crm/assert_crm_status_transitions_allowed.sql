{{ config(tags=['crm_data']) }}
-- Every status change after the first uses an allowed transition with its catalogued id and trigger event.
select h.status_history_id
from (select *, row_number() over (partition by lead_id order by changed_at, status_history_id) rn from {{ ref('stg_crm__lead_status_history') }}) h
left join {{ source('crm_design', 'lead_transition_rules') }} t on t.from_status = h.from_status and t.to_status = h.to_status
where (h.rn = 1 and (h.from_status is not null or h.to_status <> 'NEW'))
   or (h.rn > 1 and (t.transition_id is null or t.transition_id <> h.transition_id or t.trigger_event <> h.event_code))
