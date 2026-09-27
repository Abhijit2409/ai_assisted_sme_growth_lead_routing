-- The 41-CMA employer-location and Unclassified totals must equal the values recorded by source validation.
with profile as (select * from {{ ref('stg_validated__employment_size_profile_1097') }} where size_member_id = 1),
scale as (select sum(locations) as total from {{ ref('fact_cma_market_scale_snapshot') }} where size_scope = 'all_sizes'),
unclassified as (select sum(locations_all_sizes) as total from {{ ref('fact_cma_sector_composition_snapshot') }} where naics_sector = 'UNCLASSIFIED')
select profile.locations_all_41_cmas, scale.total as scale_total, profile.unclassified_all_41_cmas, unclassified.total as unclassified_total
from profile, scale, unclassified
where scale.total <> profile.locations_all_41_cmas or unclassified.total <> profile.unclassified_all_41_cmas
