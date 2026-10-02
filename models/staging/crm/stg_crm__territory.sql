-- One row per territory (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(territory_id as varchar)     as territory_id,
    cast(territory_code as varchar)   as territory_code,
    cast(territory_type as varchar)   as territory_type,
    cast(cma_code_sgc2021 as varchar) as cma_code_sgc2021,
    cast(province_code as varchar)    as province_code,
    cast(sales_region as varchar)     as sales_region,
    cast(evidence_tier as bigint)     as evidence_tier,
    cast(created_at as timestamp)     as created_at,
    cast(updated_at as timestamp)     as updated_at,
    cast(record_source as varchar)    as record_source
from {{ source('synthetic_crm', 'crm_territory') }}
