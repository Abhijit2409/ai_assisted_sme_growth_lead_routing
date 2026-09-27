-- Grain: one row per SGC 2021 CMA (41).
-- Derives each CMA's evidence tier from source coverage (0722 momentum history, 1097 scale, ISED CMA row)
-- through the reviewed crosswalks only; a test checks the result against the approved spine.
with vintage as (
    select * from {{ ref('int_market__cma_vintage_alignment') }}
),
geo as (
    select geo_code, dguid, province_code
    from {{ ref('stg_validated__geography_profile_1097') }}
    where geo_level = 'CMA'
),
momentum_cmas as (
    select distinct cma_code_sgc2016 from {{ ref('stg_statcan__business_dynamics') }} where cma_code_sgc2016 is not null
),
scale_cmas as (
    select distinct cma_code_sgc2021 from {{ ref('stg_statcan__business_locations') }} where cma_code_sgc2021 is not null
),
ised_cmas as (
    select target_code, source_label, boundary_change_flag from {{ ref('stg_reference__ised_cma_to_sgc2021') }}
),
provinces as (
    -- a CMA spanning two provinces (Ottawa - Gatineau: '24;35') keeps both, in their published order
    select geo_code,
           unnest(string_split(province_code, ';'))                          as province_code_part,
           unnest(range(1, len(string_split(province_code, ';')) + 1))        as part_position
    from geo
),
region_keys as (
    select p.geo_code, string_agg(r.ised_region_key, '|' order by p.part_position) as ised_region_key
    from provinces as p
    join {{ ref('int_financing__province_region_keys') }} as r on r.province_code = p.province_code_part
    group by p.geo_code
),
joined as (
    select
        v.*,
        geo.province_code,
        geo.dguid                              as dguid_1097_profile,
        m.cma_code_sgc2016 is not null         as has_momentum_0722,
        s.cma_code_sgc2021 is not null         as has_scale_1097,
        i.target_code is not null              as has_ised_cma_context,
        i.source_label                         as ised_cma_label,
        i.boundary_change_flag                 as ised_geography_caveat,
        rk.ised_region_key
    from vintage as v
    left join geo on geo.geo_code = v.cma_code_sgc2021
    left join momentum_cmas as m on m.cma_code_sgc2016 = v.cma_code_sgc2016
    left join scale_cmas as s on s.cma_code_sgc2021 = v.cma_code_sgc2021
    left join ised_cmas as i on i.target_code = v.cma_code_sgc2021
    left join region_keys as rk on rk.geo_code = v.cma_code_sgc2021
)
select
    *,
    cast(case when has_momentum_0722 and has_ised_cma_context then 1
              when has_momentum_0722 then 2
              when has_scale_1097 then 3 end as bigint)                    as tier
from joined
