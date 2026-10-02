-- One row per duplicate group (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(duplicate_group_id as varchar) as duplicate_group_id,
    cast(match_rule_id as varchar)      as match_rule_id,
    cast(match_tier as varchar)         as match_tier,
    cast(review_decision as varchar)    as review_decision,
    cast(surviving_lead_id as varchar)  as surviving_lead_id,
    cast(member_count as bigint)        as member_count,
    cast(flagged_at as timestamp)       as flagged_at,
    cast(resolved_at as timestamp)      as resolved_at,
    cast(matched_lead_id as varchar)    as matched_lead_id,
    cast(created_at as timestamp)       as created_at,
    cast(updated_at as timestamp)       as updated_at,
    cast(record_source as varchar)      as record_source
from {{ source('synthetic_crm', 'crm_duplicate_group') }}
