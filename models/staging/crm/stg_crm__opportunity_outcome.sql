-- One row per opportunity (at most one per lead) (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(opportunity_id as varchar)     as opportunity_id,
    cast(lead_id as varchar)            as lead_id,
    cast(rm_id as varchar)              as rm_id,
    cast(opened_at as timestamp)        as opened_at,
    cast(opportunity_status as varchar) as opportunity_status,
    cast(primary_product as varchar)    as primary_product,
    cast(closed_at as timestamp)        as closed_at,
    cast(outcome_reason as varchar)     as outcome_reason,
    cast(created_at as timestamp)       as created_at,
    cast(updated_at as timestamp)       as updated_at,
    cast(record_source as varchar)      as record_source
from {{ source('synthetic_crm', 'crm_opportunity_outcome') }}
