-- SME size-band shares reconcile to 1 and the bands add to the scale snapshot; 500+ is outside the SME share.
-- Tolerance: 1e-12 absolute on sums of 4 double-precision shares (floating-point summation only).
with f as (select * from {{ ref('fact_cma_size_composition_snapshot') }}),
per_cma as (
    select cma_code_sgc2021,
           sum(share_of_sme_locations) as share_total,
           sum(locations) filter (where in_sme_scope) as sme_locations,
           sum(locations) as all_locations,
           count(*) filter (where size_band_key = '500-plus' and share_of_sme_locations is not null) as shares_on_500_plus
    from f group by cma_code_sgc2021
),
scale as (
    select cma_code_sgc2021,
           max(locations) filter (where size_scope = 'sme_1_499') as sme,
           max(locations) filter (where size_scope = 'all_sizes') as all_sizes,
           max(locations) filter (where size_scope = '500_plus') as large
    from {{ ref('fact_cma_market_scale_snapshot') }} group by cma_code_sgc2021
)
select p.*, s.sme, s.all_sizes
from per_cma as p join scale as s using (cma_code_sgc2021)
where abs(p.share_total - 1) > 1e-12 or p.sme_locations <> s.sme or p.all_locations <> s.all_sizes
   or p.shares_on_500_plus > 0 or s.all_sizes <> s.sme + s.large
