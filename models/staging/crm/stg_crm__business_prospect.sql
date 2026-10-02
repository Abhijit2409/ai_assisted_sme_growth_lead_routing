-- One row per synthetic business prospect (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(prospect_id as varchar)                as prospect_id,
    cast(synthetic_business_id as varchar)      as synthetic_business_id,
    cast(legal_name as varchar)                 as legal_name,
    cast(normalized_legal_name as varchar)      as normalized_legal_name,
    cast(operating_name as varchar)             as operating_name,
    cast(employee_band as varchar)              as employee_band,
    cast(industry_system as varchar)            as industry_system,
    cast(industry_code as varchar)              as industry_code,
    cast(country_code as varchar)               as country_code,
    cast(existing_relationship_flag as boolean) as existing_relationship_flag,
    cast(created_at as timestamp)               as created_at,
    cast(updated_at as timestamp)               as updated_at,
    cast(record_source as varchar)              as record_source
from {{ source('synthetic_crm', 'crm_business_prospect') }}
