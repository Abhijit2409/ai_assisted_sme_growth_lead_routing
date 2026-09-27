-- Sector shares use the classified 19-sector SME denominator and reconcile. Tolerance: 1e-12 absolute on sums of
-- 19 double-precision shares (floating-point summation only; observed deviation about 2e-16).
with f as (select * from {{ ref('fact_cma_sector_composition_snapshot') }}),
per_cma as (
    select cma_code_sgc2021,
           sum(share) filter (where share_metric_id = 'S03') as share_total,
           count(*) filter (where sector_role = 'business_sector') as business_sectors,
           sum(sme_locations) filter (where sector_role = 'business_sector') as classified_sme,
           max(share_denominator) filter (where sector_role = 'business_sector') as denominator,
           sum(sme_locations) as all_sectors_sme
    from f group by cma_code_sgc2021
)
select 'share total or denominator' as issue, p.cma_code_sgc2021, p.share_total as value
from per_cma as p
join {{ ref('fact_cma_market_scale_snapshot') }} as s on s.cma_code_sgc2021 = p.cma_code_sgc2021 and s.size_scope = 'sme_1_499'
where abs(p.share_total - 1) > 1e-12 or p.business_sectors <> 19 or p.classified_sme <> p.denominator
   or p.all_sectors_sme <> s.locations
union all
select 'share differs from locations / denominator', cma_code_sgc2021, share from f
where share_metric_id = 'S03' and abs(share - sme_locations::double / share_denominator) > 1e-12
union all
select 'Unclassified or 91 allocated into the sector shares', cma_code_sgc2021, share from f
where naics_sector in ('91', 'UNCLASSIFIED') and share_metric_id = 'S03'
