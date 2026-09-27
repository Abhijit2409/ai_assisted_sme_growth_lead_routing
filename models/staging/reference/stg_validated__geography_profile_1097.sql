-- 1097 geographies with SGC 2021 code, level, DGUID and province code(s).
select geo_code, geo as geo_label, geo_level, dguid, province_code, parent_cma_code
from {{ source('validated_profiles', 'geography_profile_1097') }}
