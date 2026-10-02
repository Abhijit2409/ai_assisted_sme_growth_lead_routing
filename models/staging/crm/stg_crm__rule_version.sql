-- One row per rule set x version (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(rule_version_id as varchar)  as rule_version_id,
    cast(rule_set as varchar)         as rule_set,
    cast(version_number as bigint)    as version_number,
    cast(effective_from as timestamp) as effective_from,
    cast(effective_to as timestamp)   as effective_to,
    cast(config_sha256 as varchar)    as config_sha256,
    cast(approved_by_role as varchar) as approved_by_role,
    cast(created_at as timestamp)     as created_at,
    cast(updated_at as timestamp)     as updated_at,
    cast(record_source as varchar)    as record_source
from {{ source('synthetic_crm', 'crm_rule_version') }}
