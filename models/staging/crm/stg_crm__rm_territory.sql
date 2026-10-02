-- One row per RM x territory x effective period (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(rm_territory_id as varchar) as rm_territory_id,
    cast(rm_id as varchar)           as rm_id,
    cast(territory_id as varchar)    as territory_id,
    cast(coverage_role as varchar)   as coverage_role,
    cast(valid_from as date)         as valid_from,
    cast(valid_to as date)           as valid_to,
    cast(created_at as timestamp)    as created_at,
    cast(updated_at as timestamp)    as updated_at,
    cast(record_source as varchar)   as record_source
from {{ source('synthetic_crm', 'crm_rm_territory') }}
