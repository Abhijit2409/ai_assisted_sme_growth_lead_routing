-- Grain: one row per CMA x month x size member x industry member x measure, for the 35 CMAs with 0722 history,
-- the 19 momentum industry members and the 4 measures published for CMAs (1,891,432 rows).
-- Reopenings, entrants, temporary closures and exits carry no CMA values and are excluded.
select
    d.cma_code_sgc2016,
    d.ref_month,
    d.size_member_id,
    d.industry_member_id,
    d.measure_member_id,
    d.value_number_parsed                 as value_num,
    d.status                              as status_raw,
    d.vector
from {{ ref('stg_statcan__business_dynamics') }} as d
where d.cma_code_sgc2016 is not null
  and d.measure_member_id in (select cast(measure_member_id as integer) from {{ ref('statcan_0722_measure_members') }}
                              where in_cma_momentum_scope = 'True')
  and d.industry_member_id in (select industry_member_id from {{ ref('int_market__momentum_scope_members') }})
