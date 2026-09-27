-- One row per published 1097 geography x size member x NAICS member (1,050,274 rows).
-- Counts are employer statistical locations. No cell is suppressed and no zero is published;
-- omitted cells are handled downstream (zero by reconciliation only inside a listed CMA).
select
    '{{ var("statcan_1097_source_id") }}'                           as source_id,
    cast(REF_DATE as varchar)                                       as ref_date,
    cast(DGUID as varchar)                                          as dguid,
    case when DGUID like '2021S0503%' then right(DGUID, 3) end      as cma_code_sgc2021,
    cast(geo_id as integer)                                         as geo_member_id,
    cast(GEO as varchar)                                            as geo_label,
    cast(size_id as integer)                                        as size_member_id,
    cast("Employment size" as varchar)                              as size_label,
    cast(naics_id as integer)                                       as naics_member_id,
    cast("North American Industry Classification System (NAICS)" as varchar) as naics_label,
    cast(VECTOR as varchar)                                         as vector,
    cast(VALUE as varchar)                                          as value_text,
    coalesce(cast(STATUS as varchar), '')                           as status,
    cast(SYMBOL as varchar)                                         as symbol,
    cast(value_num as double)                                       as locations,
    'location'                                                      as statistical_unit
from {{ source('statcan_cache', 'business_locations') }}
