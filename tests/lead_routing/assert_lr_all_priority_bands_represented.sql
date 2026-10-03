{{ config(tags=['lead_routing']) }}
-- Required test: P1, P2 and P3 are all represented, and the declared bands form three non-degenerate ranges.
select band_code as issue, 'band not represented in the recommendations' as detail
from {{ ref('lead_priority_band_rules') }} b
where not exists (select 1 from {{ ref('mart_lead_priority') }} p where p.recommended_priority_band = b.band_code)
union all
select 'band ranges invalid', cast(bad as varchar) || '|' || cast(n as varchar) from (
    select sum(case when max_score_exclusive <= min_score_inclusive then 1 else 0 end) bad,
           count(*) n from {{ ref('lead_priority_band_rules') }}
) where bad > 0 or n <> 3
