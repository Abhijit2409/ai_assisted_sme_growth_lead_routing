-- One row per lead status change (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(status_history_id as varchar)    as status_history_id,
    cast(lead_id as varchar)              as lead_id,
    cast(from_status as varchar)          as from_status,
    cast(to_status as varchar)            as to_status,
    cast(transition_id as varchar)        as transition_id,
    cast(event_code as varchar)           as event_code,
    cast(changed_at as timestamp)         as changed_at,
    cast(disposition_code as varchar)     as disposition_code,
    cast(actor_role as varchar)           as actor_role,
    cast(reopen_approver_role as varchar) as reopen_approver_role,
    cast(status_reason as varchar)        as status_reason,
    cast(created_at as timestamp)         as created_at,
    cast(updated_at as timestamp)         as updated_at,
    cast(record_source as varchar)        as record_source
from {{ source('synthetic_crm', 'crm_lead_status_history') }}
