-- One row per channel (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(channel_code as varchar)              as channel_code,
    cast(channel_name as varchar)              as channel_name,
    cast(default_assignment_sla_id as varchar) as default_assignment_sla_id,
    cast(created_at as timestamp)              as created_at,
    cast(updated_at as timestamp)              as updated_at,
    cast(record_source as varchar)             as record_source
from {{ source('synthetic_crm', 'crm_acquisition_channel') }}
