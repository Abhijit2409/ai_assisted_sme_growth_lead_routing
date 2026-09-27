-- Grain: one row per table-0722 industry member in the momentum view (19):
-- the business-sector total (member 1, T004) plus the 18 sector-level members of the additive rollup (E3).
-- Tourism members, subsectors 311/312 and the second business-sector aggregate overlap sectors and are excluded.
select distinct cast(source_member_id as integer) as industry_member_id
from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
where source_class = 'naics_sector' and sector_rollup_flag = 'True'
union
select 1
