{{ config(tags=['crm_data']) }}
-- Each lead's history chains without gaps and ends at the lead's current status.
with h as (select *, lag(to_status) over (partition by lead_id order by changed_at, status_history_id) prev_to,
                  row_number() over (partition by lead_id order by changed_at desc, status_history_id desc) rn_desc
           from {{ ref('stg_crm__lead_status_history') }})
select h.lead_id from h where h.prev_to is not null and h.from_status <> h.prev_to
union all
select l.lead_id from {{ ref('stg_crm__lead') }} l join h on h.lead_id = l.lead_id and h.rn_desc = 1 where h.to_status <> l.current_lead_status
