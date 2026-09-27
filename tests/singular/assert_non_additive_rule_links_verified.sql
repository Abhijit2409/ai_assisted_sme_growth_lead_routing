-- Each of the 28 E7 non-additive rules must be linked to exactly the coded member whose label it names.
with links as (select * from {{ ref('non_additive_rule_links') }}),
labels as (
    select 'NAICS2022_1097' as industry_system, 'UNCLASSIFIED' as industry_code, naics_label || ' [' || naics_member_id_text || ']' as label
    from {{ ref('stg_validated__naics_profile_1097') }} where naics_level = 'unclassified'
    union all
    select distinct 'STATCAN_0722', source_member_id, source_label from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
    union all
    select 'ISED_SFGSME_2023', industry_code, published_label from {{ ref('ised_industry_aggregate_codes') }}
    union all
    select 'ISED_REGION', region_key, ised_region_label from {{ ref('int_financing__ised_region_keys') }}
)
select r.rule_id, r.source_label, labels.label as linked_label
from {{ ref('stg_reference__non_additive_category_rules') }} as r
left join links on links.rule_id = r.rule_id
left join labels on labels.industry_system = links.industry_system and labels.industry_code = links.industry_code
where labels.label is distinct from r.source_label
union all
select rule_id, 'link without an E7 rule', null from links
where rule_id not in (select rule_id from {{ ref('stg_reference__non_additive_category_rules') }})
