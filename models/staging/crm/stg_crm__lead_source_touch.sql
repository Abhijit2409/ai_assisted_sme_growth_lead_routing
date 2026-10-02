-- One row per lead source touch (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(source_touch_id as varchar)       as source_touch_id,
    cast(lead_id as varchar)               as lead_id,
    cast(original_lead_id as varchar)      as original_lead_id,
    cast(channel_code as varchar)          as channel_code,
    cast(touched_at as timestamp)          as touched_at,
    cast(campaign_id as varchar)           as campaign_id,
    cast(partner_code as varchar)          as partner_code,
    cast(referring_branch_code as varchar) as referring_branch_code,
    cast(prospecting_rm_id as varchar)     as prospecting_rm_id,
    cast(is_first_touch as boolean)        as is_first_touch,
    cast(created_at as timestamp)          as created_at,
    cast(updated_at as timestamp)          as updated_at,
    cast(record_source as varchar)         as record_source
from {{ source('synthetic_crm', 'crm_lead_source_touch') }}
