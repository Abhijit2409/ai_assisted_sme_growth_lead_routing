-- 1097 NAICS 2022 members with their level and derived parent (from the table metadata).
select
    cast(naics_id as integer)          as naics_member_id,
    naics_id                           as naics_member_id_text,
    naics_code,
    naics                              as naics_label,
    naics_level,
    cast(derived_parent_id as integer) as parent_naics_member_id
from {{ source('validated_profiles', 'naics_profile_1097') }}
