-- Grain: one row per CMA x framework component with its raw (untransformed) value.
-- Scale (C01) and commercial fit (C15) exist for all 41 CMAs; the momentum components (C03, C05, C09, C10) exist
-- only for the 35 CMAs with 0722 history. Tier 3 CMAs get no momentum row: nothing is imputed.
-- Every value comes from an existing dbt mart; windows are project variables. Floating-point aggregates use ORDER BY so
-- that DuckDB adds values in a fixed order: results are then bit-identical across builds (parallel order is not).
with registry as (
    select * from {{ ref('opportunity_indicator_registry') }}
),
scale as (
    select cma_code_sgc2021, cast(locations as double) as raw_value, 1 as usable_observations, 1 as required_observations, 0 as e_flagged_observations
    from {{ ref('fact_cma_market_scale_snapshot') }}
    where size_scope = 'sme_1_499'
),
commercial_fit as (
    select cma_code_sgc2021, share_of_sme_locations as raw_value, 1 as usable_observations, 1 as required_observations, 0 as e_flagged_observations
    from {{ ref('fact_cma_size_composition_snapshot') }}
    where size_band_key = '20-99'
),
series as (
    select cma_code_sgc2021, ref_month, measure, value, caution_flag,
           cast(substr(ref_month, 1, 4) as integer) * 12 + cast(substr(ref_month, 6, 2) as integer) as calendar_month_index
    from {{ ref('fact_cma_momentum_monthly') }}
    where industry_code_0722 = '1' and size_band_key = 'TOTAL' and measure in ('opening_rate', 'net_formation_rate')
),
recent as (
    select cma_code_sgc2021, measure,
           avg(value order by ref_month) as mean_value, stddev_samp(value order by ref_month) as sd_value, count(value) as usable_observations,
           cast(sum(case when caution_flag then 1 else 0 end) as integer) as e_flagged_observations
    from series
    where ref_month between '{{ var("opportunity_recent_start") }}' and '{{ var("opportunity_recent_end") }}'
    group by all
),
trend as (
    select cma_code_sgc2021,
           regr_slope(value, calendar_month_index order by ref_month) * 12 as slope_per_year, count(value) as usable_observations,
           cast(sum(case when caution_flag then 1 else 0 end) as integer) as e_flagged_observations
    from series
    where measure = 'net_formation_rate'
      and ref_month between '{{ var("opportunity_trend_start") }}' and '{{ var("opportunity_recent_end") }}'
    group by all
),
unioned as (
    select cma_code_sgc2021, 'C01_LOG_SCALE' as component_id, raw_value, usable_observations, required_observations, e_flagged_observations from scale
    union all
    select cma_code_sgc2021, 'C15_SHARE_20_99', raw_value, usable_observations, required_observations, e_flagged_observations from commercial_fit
    union all
    select cma_code_sgc2021, 'C03_OPENING_RATE', mean_value, usable_observations, {{ var("opportunity_recent_months") }}, e_flagged_observations
    from recent where measure = 'opening_rate'
    union all
    select cma_code_sgc2021, 'C05_NET_FORMATION_RATE', mean_value, usable_observations, {{ var("opportunity_recent_months") }}, e_flagged_observations
    from recent where measure = 'net_formation_rate'
    union all
    select cma_code_sgc2021, 'C09_NET_FORMATION_TREND', slope_per_year, usable_observations, {{ var("opportunity_trend_min_months") }}, e_flagged_observations
    from trend
    union all
    select cma_code_sgc2021, component_id, sd_value, usable_observations, {{ var("opportunity_recent_months") }}, e_flagged_observations
    from recent cross join (select unnest(['C10_VOLATILITY_SCALE_ADJUSTED', 'C10_VOLATILITY_RAW']) as component_id)
    where measure = 'net_formation_rate'
)
select
    u.cma_code_sgc2021,
    u.component_id,
    r.indicator_id,
    case when u.usable_observations >= u.required_observations then u.raw_value end            as raw_value,
    u.usable_observations,
    u.required_observations,
    u.e_flagged_observations,
    case when u.usable_observations >= u.required_observations then 'complete'
         else 'insufficient observations (not scored)' end                                     as input_status,
    r.source_model,
    r.source_columns,
    r.period,
    r.statistical_unit
from unioned as u
join registry as r using (component_id)
