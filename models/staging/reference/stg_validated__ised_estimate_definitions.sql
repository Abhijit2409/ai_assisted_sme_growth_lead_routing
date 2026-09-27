-- One row per ISED measure: block title, header path, unit and universe.
select measure_id, block_title, header_path, unit, universe_or_denominator, estimate_type
from {{ source('validated_profiles', 'ised_estimate_definitions') }}
