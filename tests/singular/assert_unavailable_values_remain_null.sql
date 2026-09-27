-- Suppressed ('x'), not-available ('..'), blank and quality-F observations must stay unavailable (NULL), never zero.
select 'staging 0722 unavailable value not null' as issue, count(*) as rows_failing
from {{ ref('stg_statcan__business_dynamics') }} where status in ('x', '..') and value is not null having count(*) > 0
union all
select 'momentum unavailable state with a value', count(*) from {{ ref('fact_cma_momentum_monthly') }}
where value_state in ('suppressed_x', 'not_available_dotdot', 'unavailable_input', 'no_prior_month', 'unexpected_status')
  and value is not null having count(*) > 0
union all
select 'momentum value_available disagrees with value', count(*) from {{ ref('fact_cma_momentum_monthly') }}
where value_available <> (value is not null) having count(*) > 0
union all
select 'momentum unexpected status', count(*) from {{ ref('fact_cma_momentum_monthly') }}
where value_state = 'unexpected_status' having count(*) > 0
union all
select 'ISED dotdot or blank with a value', count(*) from {{ ref('fact_ised_financing_context') }}
where value_state in ('dotdot', 'blank') and (value_published is not null or value_usable is not null) having count(*) > 0
union all
select 'ISED F estimate with a usable value', count(*) from {{ ref('fact_ised_financing_context') }}
where excluded_quality_f and value_usable is not null having count(*) > 0
