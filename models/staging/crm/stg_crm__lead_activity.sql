-- One row per activity (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(activity_id as varchar)               as activity_id,
    cast(lead_id as varchar)                   as lead_id,
    cast(assignment_id as varchar)             as assignment_id,
    cast(rm_id as varchar)                     as rm_id,
    cast(activity_at as timestamp)             as activity_at,
    cast(activity_type as varchar)             as activity_type,
    cast(activity_outcome as varchar)          as activity_outcome,
    cast(counts_as_contact_attempt as boolean) as counts_as_contact_attempt,
    cast(next_contact_due_at as timestamp)     as next_contact_due_at,
    cast(created_at as timestamp)              as created_at,
    cast(updated_at as timestamp)              as updated_at,
    cast(record_source as varchar)             as record_source
from {{ source('synthetic_crm', 'crm_lead_activity') }}
