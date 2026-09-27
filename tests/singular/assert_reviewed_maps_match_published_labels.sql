-- The reviewed code maps (constants in the Python layer, now seeds) must agree with the labels the sources publish.
with sizes as (select distinct size_member_id, size_label from {{ ref('stg_statcan__business_dynamics') }}),
measures as (select distinct measure_member_id, measure_label from {{ ref('stg_statcan__business_dynamics') }})
select 'table-0722 size member label differs' as issue, s.published_label as detail
from {{ ref('statcan_0722_size_members') }} as s
left join sizes on sizes.size_member_id = cast(s.size_member_id as integer)
where sizes.size_label is distinct from s.published_label
union all
select 'table-0722 measure member label differs', m.published_label
from {{ ref('statcan_0722_measure_members') }} as m
left join measures on measures.measure_member_id = cast(m.measure_member_id as integer)
where measures.measure_label is distinct from m.published_label
union all
select 'ISED size row label not in reviewed map', stub_label
from (select distinct stub_label from {{ ref('stg_ised__financing_context') }} where dimension_class = 'employment_size')
where stub_label not in (select published_label from {{ ref('ised_size_segments') }})
union all
select 'reviewed ISED size label not published', published_label from {{ ref('ised_size_segments') }}
where published_label not in (select stub_label from {{ ref('stg_ised__financing_context') }} where dimension_class = 'employment_size')
union all
select 'ISED national row label missing', '{{ var("ised_national_label") }}'
where not exists (select 1 from {{ ref('stg_ised__financing_context') }} where dimension_class = 'all_smes' and stub_label = '{{ var("ised_national_label") }}')
union all
select 'ISED aggregate without a reviewed code', source_label from {{ ref('stg_reference__ised_industry_to_naics2022') }}
where source_code is null and source_label not in (select published_label from {{ ref('ised_industry_aggregate_codes') }})
union all
select 'reviewed aggregate code without E4 row', published_label from {{ ref('ised_industry_aggregate_codes') }}
where published_label not in (select source_label from {{ ref('stg_reference__ised_industry_to_naics2022') }} where source_code is null)
