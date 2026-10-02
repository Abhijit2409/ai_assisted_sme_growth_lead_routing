-- One row per campaign (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(campaign_id as varchar)      as campaign_id,
    cast(campaign_code as varchar)    as campaign_code,
    cast(campaign_name as varchar)    as campaign_name,
    cast(target_segment as varchar)   as target_segment,
    cast(campaign_start_date as date) as campaign_start_date,
    cast(campaign_end_date as date)   as campaign_end_date,
    cast(created_at as timestamp)     as created_at,
    cast(updated_at as timestamp)     as updated_at,
    cast(record_source as varchar)    as record_source
from {{ source('synthetic_crm', 'crm_campaign') }}
