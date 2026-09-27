-- Reviewed crosswalk E5: one row per 1097 size member (8 bands + total).
select * replace (
    cast(source_lower as bigint) as source_lower, cast(source_upper as bigint) as source_upper,
    cast(target_lower as bigint) as target_lower, cast(target_upper as bigint) as target_upper)
from {{ ref('statcan_1097_size_to_sme_bands') }}
