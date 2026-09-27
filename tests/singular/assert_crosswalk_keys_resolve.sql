-- Unmatched source keys and invalid target codes: every source member used downstream has a reviewed mapping,
-- and every mapped target exists in the target classification.
with nt as (select naics_code from {{ ref('stg_validated__naics_profile_1097') }} where naics_code is not null),
cma_1097 as (select distinct cma_code_sgc2021 from {{ ref('stg_statcan__business_locations') }} where cma_code_sgc2021 is not null)
select '0722 CMA code without E1 mapping' as issue, cma_code_sgc2016 as key
from (select distinct cma_code_sgc2016 from {{ ref('stg_statcan__business_dynamics') }} where cma_code_sgc2016 is not null)
where cma_code_sgc2016 not in (select source_code from {{ ref('stg_reference__cma_sgc2016_to_sgc2021') }} where source_code is not null)
union all
select 'E1 target is not a 1097 CMA', target_code from {{ ref('stg_reference__cma_sgc2016_to_sgc2021') }}
where target_code not in (select cma_code_sgc2021 from cma_1097)
union all
select '1097 CMA without E1 mapping', cma_code_sgc2021 from cma_1097
where cma_code_sgc2021 not in (select target_code from {{ ref('stg_reference__cma_sgc2016_to_sgc2021') }})
union all
select '0722 industry member without E3 mapping', cast(industry_member_id as varchar)
from (select distinct industry_member_id from {{ ref('stg_statcan__business_dynamics') }})
where cast(industry_member_id as varchar) not in (select source_member_id from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }})
union all
select 'E3 target not a NAICS 2022 code', target_code from {{ ref('stg_reference__statcan_0722_industry_to_naics2022') }}
where target_code is not null and target_code not in (select naics_code from nt)
union all
select 'E4 target not a NAICS 2022 code', target_code from {{ ref('stg_reference__ised_industry_to_naics2022') }}
where target_code is not null and target_code not in (select naics_code from nt)
union all
select '1097 size member without E5 mapping', cast(size_member_id as varchar)
from (select distinct size_member_id from {{ ref('stg_statcan__business_locations') }})
where cast(size_member_id as varchar) not in (select source_code from {{ ref('stg_reference__statcan_1097_size_to_sme_bands') }})
union all
select 'E5 target not a broad band', target_code from {{ ref('stg_reference__statcan_1097_size_to_sme_bands') }}
where target_code not in (select size_band_key from {{ ref('broad_size_band_order') }})
union all
select 'E2 target not a 1097 CMA', target_code from {{ ref('stg_reference__ised_cma_to_sgc2021') }}
where target_code not in (select cma_code_sgc2021 from cma_1097)
union all
select 'ISED CMA row without E2 mapping', stub_label
from (select distinct stub_label from {{ ref('stg_ised__financing_context') }} where dimension_class = 'geography_cma')
where stub_label not in (select source_label from {{ ref('stg_reference__ised_cma_to_sgc2021') }})
union all
select 'ISED region row without E8 mapping', stub_label
from (select distinct stub_label from {{ ref('stg_ised__financing_context') }} where dimension_class = 'geography_region')
where stub_label not in (select source_label from {{ ref('stg_reference__ised_region_province_crosswalk') }})
union all
select 'ISED industry row without E4 mapping', stub_label
from (select distinct stub_label from {{ ref('stg_ised__financing_context') }} where dimension_class = 'industry_naics_group')
where stub_label not in (select source_label from {{ ref('stg_reference__ised_industry_to_naics2022') }})
