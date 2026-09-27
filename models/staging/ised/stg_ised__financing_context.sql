-- One row per ISED table x measure x stub row (79,876 value cells).
-- Keeps the published stub text, quality code, printed error (SE or CV) and value state.
-- '..' and blank cells have a NULL value and are never zero.
select
    '{{ var("ised_source_id") }}'                                   as source_id,
    cast({{ var("ised_survey_year") }} as bigint)                   as survey_year,
    cast(table_no as varchar)                                       as table_no,
    cast(table_id as varchar)                                       as table_id,
    cast(measure_id as varchar)                                     as measure_id,
    cast(measure_idx as bigint)                                     as measure_index,
    cast(block_no as bigint)                                        as block_no,
    cast(unit_token as varchar)                                     as unit_token,
    cast(estimate_type as varchar)                                  as estimate_type,
    cast(stub_row as bigint)                                        as stub_row,
    cast("group" as varchar)                                        as stub_group,
    cast(label as varchar)                                          as stub_label,
    cast(level as varchar)                                          as stub_level,
    cast(value as double)                                           as value_published,
    cast(value_state as varchar)                                    as value_state,
    case when quality_state = 'quality_code' then cast(quality as varchar) end as quality_code,
    cast(quality_state as varchar)                                  as quality_state,
    cast(error as double)                                           as error_value,
    cast(error_state as varchar)                                    as error_state,
    cast(error_type as varchar)                                     as error_type,
    cast(dimension_class as varchar)                                as dimension_class,
    cast(geography_level as varchar)                                as geography_level,
    cast(dotdot_context as varchar)                                 as dotdot_context,
    cast(availability_dimension as varchar)                         as availability_dimension
from {{ source('ised_cache', 'financing_cells') }}
