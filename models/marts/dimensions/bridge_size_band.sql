-- One row per 1097 size member (8 bands + total) mapped to its broad band. Location basis only.
select
    source_code                      as source_band,
    source_label,
    target_code                      as target_band,
    target_label,
    source_lower,
    source_upper,
    mapping_type,
    mapping_status,
    unit_basis,
    case in_ised_scope when 'True' then true when 'False' then false end as in_ised_scope,
    crosswalk_id
from {{ ref('stg_reference__statcan_1097_size_to_sme_bands') }}
order by source_band, target_band
