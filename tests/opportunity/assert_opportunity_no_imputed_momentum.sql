{{ config(tags=['opportunity']) }}
-- Tier 3 CMAs receive no momentum input (nothing imputed), and every scored CMA has complete inputs.
select 'momentum input for a Tier 3 CMA' as issue, i.cma_code_sgc2021, i.component_id
from {{ ref('int_opportunity__indicator_inputs') }} as i
join {{ ref('dim_cma') }} as d using (cma_code_sgc2021)
where d.tier = 3 and i.component_id not in ('C01_LOG_SCALE', 'C15_SHARE_20_99')
union all
select 'scored CMA with an incomplete input', cma_code_sgc2021, component_id
from {{ ref('int_opportunity__normalized_indicators') }}
where input_status <> 'complete' or raw_value is null or transformed_value is null or normalized_percentile is null
