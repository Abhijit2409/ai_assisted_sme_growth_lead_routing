-- Every seed must equal its reviewed CSV cell for cell (read as text with no null inference);
-- the only accepted difference is that an empty cell loads as NULL.
select 'cma_sgc2016_to_sgc2021' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('cma_sgc2016_to_sgc2021') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/cma_sgc2016_to_sgc2021.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'cma_sgc2016_to_sgc2021', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/cma_sgc2016_to_sgc2021.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('cma_sgc2016_to_sgc2021') }})
having count(*) > 0
union all
select 'crosswalk_schema' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('crosswalk_schema') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/crosswalk_schema.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'crosswalk_schema', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/crosswalk_schema.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('crosswalk_schema') }})
having count(*) > 0
union all
select 'crosswalk_sources' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('crosswalk_sources') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/crosswalk_sources.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'crosswalk_sources', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/crosswalk_sources.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('crosswalk_sources') }})
having count(*) > 0
union all
select 'ised_cma_to_sgc2021' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_cma_to_sgc2021') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_cma_to_sgc2021.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_cma_to_sgc2021', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_cma_to_sgc2021.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_cma_to_sgc2021') }})
having count(*) > 0
union all
select 'ised_industry_to_naics2022' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_industry_to_naics2022') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_industry_to_naics2022.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_industry_to_naics2022', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_industry_to_naics2022.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_industry_to_naics2022') }})
having count(*) > 0
union all
select 'ised_region_province_crosswalk' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_region_province_crosswalk') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_region_province_crosswalk.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_region_province_crosswalk', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_region_province_crosswalk.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_region_province_crosswalk') }})
having count(*) > 0
union all
select 'ised_scope_approximation_rules' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_scope_approximation_rules') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_scope_approximation_rules.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_scope_approximation_rules', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/ised_scope_approximation_rules.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_scope_approximation_rules') }})
having count(*) > 0
union all
select 'non_additive_category_rules' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('non_additive_category_rules') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/non_additive_category_rules.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'non_additive_category_rules', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/non_additive_category_rules.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('non_additive_category_rules') }})
having count(*) > 0
union all
select 'statcan_0722_industry_to_naics2022' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('statcan_0722_industry_to_naics2022') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/statcan_0722_industry_to_naics2022.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'statcan_0722_industry_to_naics2022', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/statcan_0722_industry_to_naics2022.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('statcan_0722_industry_to_naics2022') }})
having count(*) > 0
union all
select 'statcan_1097_size_to_sme_bands' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('statcan_1097_size_to_sme_bands') }} except all select nullif(columns(*), '') from read_csv('data/reference/crosswalks/statcan_1097_size_to_sme_bands.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'statcan_1097_size_to_sme_bands', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('data/reference/crosswalks/statcan_1097_size_to_sme_bands.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('statcan_1097_size_to_sme_bands') }})
having count(*) > 0
union all
select 'broad_size_band_order' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('broad_size_band_order') }} except all select nullif(columns(*), '') from read_csv('seeds/broad_size_band_order.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'broad_size_band_order', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/broad_size_band_order.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('broad_size_band_order') }})
having count(*) > 0
union all
select 'ised_industry_aggregate_codes' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_industry_aggregate_codes') }} except all select nullif(columns(*), '') from read_csv('seeds/ised_industry_aggregate_codes.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_industry_aggregate_codes', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/ised_industry_aggregate_codes.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_industry_aggregate_codes') }})
having count(*) > 0
union all
select 'ised_segment_scope' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_segment_scope') }} except all select nullif(columns(*), '') from read_csv('seeds/ised_segment_scope.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_segment_scope', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/ised_segment_scope.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_segment_scope') }})
having count(*) > 0
union all
select 'ised_size_segments' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_size_segments') }} except all select nullif(columns(*), '') from read_csv('seeds/ised_size_segments.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_size_segments', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/ised_size_segments.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_size_segments') }})
having count(*) > 0
union all
select 'ised_subprovincial_region_keys' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('ised_subprovincial_region_keys') }} except all select nullif(columns(*), '') from read_csv('seeds/ised_subprovincial_region_keys.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'ised_subprovincial_region_keys', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/ised_subprovincial_region_keys.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('ised_subprovincial_region_keys') }})
having count(*) > 0
union all
select 'non_additive_rule_links' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('non_additive_rule_links') }} except all select nullif(columns(*), '') from read_csv('seeds/non_additive_rule_links.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'non_additive_rule_links', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/non_additive_rule_links.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('non_additive_rule_links') }})
having count(*) > 0
union all
select 'statcan_0722_measure_members' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('statcan_0722_measure_members') }} except all select nullif(columns(*), '') from read_csv('seeds/statcan_0722_measure_members.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'statcan_0722_measure_members', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/statcan_0722_measure_members.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('statcan_0722_measure_members') }})
having count(*) > 0
union all
select 'statcan_0722_size_members' as seed_name, 'in seed, not in file' as side, count(*) as rows_differing
from (select * from {{ ref('statcan_0722_size_members') }} except all select nullif(columns(*), '') from read_csv('seeds/statcan_0722_size_members.csv', all_varchar=true, allow_quoted_nulls=false, header=true))
having count(*) > 0
union all
select 'statcan_0722_size_members', 'in file, not in seed', count(*)
from (select nullif(columns(*), '') from read_csv('seeds/statcan_0722_size_members.csv', all_varchar=true, allow_quoted_nulls=false, header=true) except all select * from {{ ref('statcan_0722_size_members') }})
having count(*) > 0
