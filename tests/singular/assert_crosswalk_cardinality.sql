-- Expected cardinality of the reviewed crosswalks: rows declared one-to-one have exactly one row per source key;
-- one-to-many relationships appear only where the crosswalk declares them.
select 'E3 one-to-one member with several rows' as issue, source_member_id as source_key, count(*) as rows_per_key
from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
group by source_member_id
having count(*) > 1 and bool_or(mapping_type = 'one-to-one')
union all
select 'E3 multi-row member not declared one-to-many', source_member_id, count(*)
from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
group by source_member_id
having count(*) > 1 and not bool_and(mapping_type = 'one-to-many')
union all
select 'E4 one-to-one group with several rows', source_label, count(*)
from {{ ref('stg_reference__ised_industry_to_naics2022') }}
group by source_label
having count(*) > 1 and bool_or(mapping_type = 'one-to-one')
union all
select 'E8 one-to-one label with several rows', source_label, count(*)
from {{ ref('stg_reference__ised_region_province_crosswalk') }}
group by source_label
having count(*) > 1 and bool_or(mapping_type = 'one-to-one')
union all
select 'E2 label mapped to several CMAs', source_label, count(*)
from {{ ref('stg_reference__ised_cma_to_sgc2021') }}
group by source_label having count(*) > 1
