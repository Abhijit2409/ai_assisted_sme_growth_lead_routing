{{ config(tags=['opportunity']) }}
-- Raw indicator values trace to the existing marts: recomputed here independently from the source marts, C09 with a
-- different slope formula (covariance / variance instead of regr_slope). Tolerance 1e-9 relative: two floating-point
-- algorithms for the same quantity differ in the last digits (observed maximum 3.5e-12 relative on a near-zero slope);
-- any real lineage error would be many orders of magnitude larger.
with s as (
    select cma_code_sgc2021, ref_month, measure, value,
           cast(substr(ref_month, 1, 4) as integer) * 12 + cast(substr(ref_month, 6, 2) as integer) as mi
    from {{ ref('fact_cma_momentum_monthly') }}
    where industry_code_0722 = '1' and size_band_key = 'TOTAL'
),
expected as (
    select cma_code_sgc2021, 'C03_OPENING_RATE' as component_id, avg(value) as v from s
    where measure = 'opening_rate' and ref_month between '{{ var("opportunity_recent_start") }}' and '{{ var("opportunity_recent_end") }}' group by 1
    union all
    select cma_code_sgc2021, 'C05_NET_FORMATION_RATE', avg(value) from s
    where measure = 'net_formation_rate' and ref_month between '{{ var("opportunity_recent_start") }}' and '{{ var("opportunity_recent_end") }}' group by 1
    union all
    select cma_code_sgc2021, 'C09_NET_FORMATION_TREND', 12 * covar_pop(value, mi) / var_pop(mi) from s
    where measure = 'net_formation_rate' and value is not null
      and ref_month between '{{ var("opportunity_trend_start") }}' and '{{ var("opportunity_recent_end") }}' group by 1
    union all
    select cma_code_sgc2021, 'C01_LOG_SCALE', locations from {{ ref('fact_cma_market_scale_snapshot') }} where size_scope = 'sme_1_499'
    union all
    select cma_code_sgc2021, 'C15_SHARE_20_99', share_of_sme_locations from {{ ref('fact_cma_size_composition_snapshot') }} where size_band_key = '20-99'
)
select i.cma_code_sgc2021, i.component_id, i.raw_value, e.v
from {{ ref('int_opportunity__indicator_inputs') }} as i
join expected as e using (cma_code_sgc2021, component_id)
where abs(i.raw_value - e.v) > 1e-9 * greatest(abs(e.v), 1e-12)
union all
select i.cma_code_sgc2021, i.component_id, i.raw_value, null
from {{ ref('int_opportunity__indicator_inputs') }} as i
where i.component_id in ('C01_LOG_SCALE', 'C03_OPENING_RATE', 'C05_NET_FORMATION_RATE', 'C09_NET_FORMATION_TREND', 'C15_SHARE_20_99') and not exists (select 1 from expected e where e.cma_code_sgc2021 = i.cma_code_sgc2021 and e.component_id = i.component_id)
