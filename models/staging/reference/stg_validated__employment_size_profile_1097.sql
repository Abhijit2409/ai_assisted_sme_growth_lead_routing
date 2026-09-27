-- 1097 size members with the 41-CMA totals recorded by source validation (reconciliation anchors).
select
    cast(size_id as integer)                    as size_member_id,
    employment_size,
    cast(locations_all_41_cmas as bigint)       as locations_all_41_cmas,
    cast(unclassified_all_41_cmas as bigint)    as unclassified_all_41_cmas
from {{ source('validated_profiles', 'employment_size_profile_1097') }}
