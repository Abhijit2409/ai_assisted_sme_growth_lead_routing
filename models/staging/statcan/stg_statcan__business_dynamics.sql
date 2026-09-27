-- One row per 0722 geography x industry member x size member x measure x month (8,575,672 rows).
-- Renames and types the validated cache; keeps codes, labels, status markers and the raw value text.
-- 'x' (suppressed) and '..' (not available) stay NULL in `value`; they are never zero.
select
    '{{ var("statcan_0722_source_id") }}'                           as source_id,
    cast(REF_DATE as varchar)                                       as ref_month,
    cast(DGUID as varchar)                                          as dguid,
    case when DGUID like '2016S0503%' then right(DGUID, 3) end      as cma_code_sgc2016,
    cast(geo_id as integer)                                         as geo_member_id,
    cast(GEO as varchar)                                            as geo_label,
    cast(industry_id as integer)                                    as industry_member_id,
    cast("Industry" as varchar)                                     as industry_label,
    cast(size_id as integer)                                        as size_member_id,
    cast("Employment size" as varchar)                              as size_label,
    cast(measure_id as integer)                                     as measure_member_id,
    cast("Business dynamics measure" as varchar)                    as measure_label,
    cast(UOM as varchar)                                            as unit_of_measure,
    cast(SCALAR_FACTOR as varchar)                                  as scalar_factor,
    cast(VECTOR as varchar)                                         as vector,
    cast(COORDINATE as varchar)                                     as coordinate,
    cast(VALUE as varchar)                                          as value_text,
    coalesce(cast(STATUS as varchar), '')                           as status,
    cast(SYMBOL as varchar)                                         as symbol,
    cast(TERMINATED as varchar)                                     as terminated,
    cast(value_num as double)                                       as value_number_parsed,
    case when coalesce(STATUS, '') in ('', 'E') then cast(value_num as double) end as value,
    case coalesce(STATUS, '')
        when '' then 'published' when 'E' then 'published_E'
        when 'x' then 'suppressed_x' when '..' then 'not_available_dotdot'
        else 'unexpected_status' end                                as value_state,
    'enterprise'                                                    as statistical_unit
from {{ source('statcan_cache', 'business_dynamics') }}
