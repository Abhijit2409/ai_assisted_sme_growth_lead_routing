-- Grain: one row per ISED row context (dimension class) x verbatim stub text.
-- ISED publishes no codes, so each stub text is matched exactly, within its row context, to the key of a
-- reviewed crosswalk (E2 CMAs, E8 regions, E4 industry groups) or reviewed map; only the code is used downstream.
-- The context keeps the "Quebec" CMA row (421) apart from the "Quebec" province row (24).
select 'geography_cma' as dimension_class, source_label as stub_label, target_code as segment_key,
       boundary_change_flag as geography_caveat
from {{ ref('stg_reference__ised_cma_to_sgc2021') }}
union all
select 'geography_region', ised_region_label, region_key,
       'ISED region; never replicated to or split among provinces (E8)'
from {{ ref('int_financing__ised_region_keys') }}
union all
select distinct 'industry_naics_group', e4.source_label, coalesce(e4.source_code, agg.industry_code), cast(null as varchar)
from {{ ref('stg_reference__ised_industry_to_naics2022') }} as e4
left join {{ ref('ised_industry_aggregate_codes') }} as agg on agg.published_label = e4.source_label
union all
select 'employment_size', published_label, size_band_key, cast(null as varchar)
from {{ ref('ised_size_segments') }}
union all
select 'all_smes', '{{ var("ised_national_label") }}', 'CANADA', cast(null as varchar)
