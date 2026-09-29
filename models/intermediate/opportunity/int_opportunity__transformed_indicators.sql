-- Grain: one row per CMA x framework component. Applies the documented transformation to the raw value:
-- log10 for scale (heavily right-skewed), identity for rates and shares, log10 of the SD for volatility, and for the
-- scale-adjusted volatility the residual of log10(SD) regressed on log10(SME locations) across the 35 full-framework CMAs.
with inputs as (
    select * from {{ ref('int_opportunity__indicator_inputs') }}
),
eligible as (
    select cma_code_sgc2021 from {{ ref('dim_cma') }} where momentum_comparison_eligible
),
log_scale as (
    select cma_code_sgc2021, log10(raw_value) as log_scale from inputs where component_id = 'C01_LOG_SCALE'
),
volatility as (
    select i.cma_code_sgc2021, log10(i.raw_value) as log_sd, s.log_scale
    from inputs as i
    join log_scale as s using (cma_code_sgc2021)
    where i.component_id = 'C10_VOLATILITY_SCALE_ADJUSTED' and i.cma_code_sgc2021 in (select cma_code_sgc2021 from eligible)
),
fit_line as (
    select regr_slope(log_sd, log_scale order by cma_code_sgc2021) as slope, regr_intercept(log_sd, log_scale order by cma_code_sgc2021) as intercept,
           count(*) as n
    from volatility
)
select
    i.*,
    r.transformation,
    case i.component_id
        when 'C01_LOG_SCALE' then log10(i.raw_value)
        when 'C10_VOLATILITY_RAW' then log10(i.raw_value)
        when 'C10_VOLATILITY_SCALE_ADJUSTED' then log10(i.raw_value) - (f.intercept + f.slope * s.log_scale)
        else i.raw_value
    end                                                                                  as transformed_value,
    case when i.component_id = 'C10_VOLATILITY_SCALE_ADJUSTED' then f.slope end          as scale_adjustment_slope,
    case when i.component_id = 'C10_VOLATILITY_SCALE_ADJUSTED' then f.intercept end      as scale_adjustment_intercept
from inputs as i
join {{ ref('opportunity_indicator_registry') }} as r using (component_id)
left join log_scale as s using (cma_code_sgc2021)
cross join fit_line as f
