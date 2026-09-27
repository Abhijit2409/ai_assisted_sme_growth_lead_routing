-- Non-additive categories (E7) and overlapping members are never marked additive, in the dimension or the bridge.
select 'dim_industry member governed by an E7 rule is additive' as issue, industry_system, industry_code
from {{ ref('dim_industry') }} where non_additive_rule_id is not null and additive_flag
union all
select 'dim_industry member is both overlapping and additive', industry_system, industry_code
from {{ ref('dim_industry') }} where overlap_flag and additive_flag
union all
select 'bridge row for an aggregate or non-reconcilable member is additive', source_system, source_code
from {{ ref('bridge_industry') }} where additive_flag and mapping_status in ('aggregate', 'non-reconcilable')
union all
select 'bridge allows value allocation', source_system, source_code
from {{ ref('bridge_industry') }} where value_allocation_allowed
