-- One row per industry system x code: 445 NAICS 2022 members (1097), 32 table-0722 members, 18 ISED groups/aggregates.
select
    industry_system, industry_code, industry_label, industry_level, parent_code, in_mvp, in_business_sector_scope,
    additive_flag, overlap_flag, mvp_role, naics_codes, classification_vintage, mapping_status, source_member_id,
    non_additive_rule_id
from {{ ref('int_market__industry_members') }}
order by industry_system, industry_code
