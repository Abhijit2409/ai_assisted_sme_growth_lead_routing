-- Grain: one row per CMA (41) x NAICS member (total, 20 sectors, Unclassified) x size member (9) = 8,118 cells.
-- Starts from the full grid so a CMA x sector cell with no locations is kept. An omitted cell inside a listed
-- CMA is zero by reconciliation (every published 1097 parent equals the sum of its children); it is flagged,
-- counted downstream and never mixed up with a published count.
with cmas as (
    select cma_code_sgc2021 from {{ ref('int_market__cma_vintage_alignment') }}
),
naics as (
    select naics_member_id, industry_code, naics_level
    from {{ ref('stg_validated__naics_profile_1097') }} as n
    join {{ ref('int_market__industry_members') }} as m
      on m.industry_system = 'NAICS2022_1097' and m.source_member_id = n.naics_member_id_text
    where n.naics_level in ('table_total', 'sector', 'unclassified')
),
sizes as (
    select cast(source_code as integer) as size_member_id, target_code as size_band_key
    from {{ ref('stg_reference__statcan_1097_size_to_sme_bands') }}
),
cells as (
    select cma_code_sgc2021, naics_member_id, locations
    , size_member_id
    from {{ ref('stg_statcan__business_locations') }}
    where cma_code_sgc2021 is not null
)
select
    g.cma_code_sgc2021,
    n.naics_member_id,
    n.industry_code,
    n.naics_level,
    s.size_member_id,
    s.size_band_key,
    c.locations                    as value_num,
    c.locations is null            as omitted
from cmas as g
cross join naics as n
cross join sizes as s
left join cells as c
  on c.cma_code_sgc2021 = g.cma_code_sgc2021 and c.naics_member_id = n.naics_member_id and c.size_member_id = s.size_member_id
