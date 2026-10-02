-- One row per synthetic RM (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(rm_id as varchar)                       as rm_id,
    cast(rm_code as varchar)                     as rm_code,
    cast(rm_segment as varchar)                  as rm_segment,
    cast(home_territory_id as varchar)           as home_territory_id,
    cast(supported_service_languages as varchar) as supported_service_languages,
    cast(base_capacity_points as decimal(6,2))   as base_capacity_points,
    cast(employment_start_date as date)          as employment_start_date,
    cast(employment_end_date as date)            as employment_end_date,
    cast(active_flag as boolean)                 as active_flag,
    cast(created_at as timestamp)                as created_at,
    cast(updated_at as timestamp)                as updated_at,
    cast(record_source as varchar)               as record_source
from {{ source('synthetic_crm', 'crm_relationship_manager') }}
