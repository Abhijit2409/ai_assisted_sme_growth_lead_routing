-- Grain: one row per industry system x industry code (495 = 445 NAICS 2022 members from 1097,
-- 32 table-0722 members, 18 ISED groups and special aggregates).
-- Scope, additivity and overlap flags keep non-additive categories from being summed.
with recursive
npf as (
    select *,
           case naics_level when 'table_total' then 'TOTAL' when 'unclassified' then 'UNCLASSIFIED'
                            when 'classified_subtotal' then 'CLASSIFIED_SUBTOTAL' else naics_code end as industry_code
    from {{ ref('stg_validated__naics_profile_1097') }}
),
ancestry (naics_member_id, current_member_id, current_level) as (
    select naics_member_id, naics_member_id, naics_level from npf
    union all
    select a.naics_member_id, p.parent_naics_member_id, parent.naics_level
    from ancestry as a
    join npf as p on p.naics_member_id = a.current_member_id
    join npf as parent on parent.naics_member_id = p.parent_naics_member_id
    where a.current_level not in ('sector', 'table_total', 'unclassified', 'classified_subtotal')
),
top_node as (
    select a.naics_member_id, n.industry_code as top_code, n.naics_level as top_level
    from ancestry as a
    join npf as n on n.naics_member_id = a.current_member_id
    where a.current_level in ('sector', 'table_total', 'unclassified', 'classified_subtotal')
),
naics_1097 as (
    select
        'NAICS2022_1097'                                        as industry_system,
        n.industry_code,
        n.naics_label                                           as industry_label,
        n.naics_level                                           as industry_level,
        parent.industry_code                                    as parent_code,
        case when n.naics_level = 'sector' and n.industry_code <> '91' then 'composition_sector'
             when n.naics_level = 'sector' then 'shown_separately_outside_sme_banking_scope'
             when n.naics_level = 'unclassified' then 'shown_separately_never_allocated'
             else 'not_in_mvp_view' end                         as mvp_role,
        t.top_level = 'sector' and t.top_code <> '91'           as in_business_sector_scope,
        n.naics_level in ('sector', 'subsector', 'industry_group') as additive_flag,
        n.naics_level in ('table_total', 'classified_subtotal') as overlap_flag,
        case when n.naics_level in ('sector', 'subsector', 'industry_group') then n.industry_code end as naics_codes,
        'NAICS 2022 (1097 Note 5)'                              as classification_vintage,
        'native'                                                as mapping_status,
        n.naics_member_id_text                                  as source_member_id
    from npf as n
    left join npf as parent on parent.naics_member_id = n.parent_naics_member_id
    left join top_node as t on t.naics_member_id = n.naics_member_id
),
e3 as (
    select * from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
),
momentum_scope as (
    select industry_member_id from {{ ref('int_market__momentum_scope_members') }}
),
statcan_0722 as (
    select
        'STATCAN_0722'                                          as industry_system,
        e3.source_member_id                                     as industry_code,
        min(e3.source_label)                                    as industry_label,
        min(e3.source_class)                                    as industry_level,
        min(e3.source_parent_id)                                as parent_code,
        case when cast(e3.source_member_id as integer) in (select industry_member_id from momentum_scope)
             then case when e3.source_member_id = '1' then 'momentum_business_sector_total' else 'momentum_sector_member' end
             else 'not_in_mvp_view' end                         as mvp_role,
        true                                                    as in_business_sector_scope,
        min(e3.sector_rollup_flag) = 'True'                     as additive_flag,
        min(e3.sector_rollup_flag) <> 'True'                    as overlap_flag,
        string_agg(e3.target_code, ';' order by e3.target_code) as naics_codes,
        'NAICS vintage not stated (0722)'                       as classification_vintage,
        case when count(distinct e3.mapping_status) = 1 then min(e3.mapping_status) else 'MIXED' end as mapping_status,
        e3.source_member_id                                     as source_member_id
    from e3
    group by e3.source_member_id
),
e4 as (
    select e4.*, coalesce(e4.source_code, agg.industry_code) as ised_industry_code
    from {{ ref('stg_reference__ised_industry_to_naics2022') }} as e4
    left join {{ ref('ised_industry_aggregate_codes') }} as agg on agg.published_label = e4.source_label
),
ised as (
    select
        'ISED_SFGSME_2023'                                      as industry_system,
        min(ised_industry_code)                                 as industry_code,
        source_label                                            as industry_label,
        case when min(inclusion_type) <> 'overlapping aggregate' then 'industry_group' else 'special_aggregate' end as industry_level,
        cast(null as varchar)                                   as parent_code,
        case when min(inclusion_type) <> 'overlapping aggregate' then 'financing_context_group' else 'not_in_mvp_view' end as mvp_role,
        true                                                    as in_business_sector_scope,
        min(additive_flag) = 'True'                             as additive_flag,
        min(inclusion_type) = 'overlapping aggregate'           as overlap_flag,
        string_agg(target_code, ';' order by target_code)       as naics_codes,
        'NAICS 2022 sectors via E4'                             as classification_vintage,
        case when count(distinct mapping_status) = 1 then min(mapping_status) else 'MIXED' end as mapping_status,
        cast(null as varchar)                                   as source_member_id
    from e4
    group by source_label
),
unioned as (
    select * from naics_1097
    union all by name select * from statcan_0722
    union all by name select * from ised
)
select
    u.*,
    u.mvp_role <> 'not_in_mvp_view'                             as in_mvp,
    links.rule_id                                               as non_additive_rule_id
from unioned as u
left join {{ ref('non_additive_rule_links') }} as links
    on links.industry_system = u.industry_system and links.industry_code = u.industry_code
