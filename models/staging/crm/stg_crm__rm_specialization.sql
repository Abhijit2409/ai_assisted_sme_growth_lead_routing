-- One row per RM x industry x effective period (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(rm_specialization_id as varchar) as rm_specialization_id,
    cast(rm_id as varchar)                as rm_id,
    cast(industry_system as varchar)      as industry_system,
    cast(industry_code as varchar)        as industry_code,
    cast(specialist_type as varchar)      as specialist_type,
    cast(valid_from as date)              as valid_from,
    cast(valid_to as date)                as valid_to,
    cast(specialization_role as varchar)  as specialization_role,
    cast(created_at as timestamp)         as created_at,
    cast(updated_at as timestamp)         as updated_at,
    cast(record_source as varchar)        as record_source
from {{ source('synthetic_crm', 'crm_rm_specialization') }}
