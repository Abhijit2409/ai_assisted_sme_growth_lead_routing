-- Every momentum rate must equal the same 0722 series' openings/closures divided by its own active enterprises
-- in the previous calendar month (never a 1097 count). Tolerance: 1e-12 absolute, only to absorb floating-point
-- representation; both sides divide the same doubles, so the observed difference is 0.
with f as (select * from {{ ref('fact_cma_momentum_monthly') }}),
pivoted as (
    select cma_code_sgc2016, ref_month, size_band_key, industry_code_0722,
           max(case when measure = 'active' then value end) as active,
           max(case when measure = 'openings' then value end) as openings,
           max(case when measure = 'closures' then value end) as closures,
           max(case when measure = 'opening_rate' then value end) as opening_rate,
           max(case when measure = 'closure_rate' then value end) as closure_rate,
           max(case when measure = 'net_formation_rate' then value end) as net_formation_rate,
           bool_or(measure = 'opening_rate') as has_rate
    from f group by all
),
with_previous as (
    select cur.*, prev.active as active_previous_month
    from pivoted as cur
    left join pivoted as prev
      on prev.cma_code_sgc2016 = cur.cma_code_sgc2016 and prev.size_band_key = cur.size_band_key
     and prev.industry_code_0722 = cur.industry_code_0722
     and prev.ref_month = strftime(strptime(cur.ref_month || '-01', '%Y-%m-%d') - interval 1 month, '%Y-%m')
),
expected as (
    select *,
           openings / nullif(active_previous_month, 0) as exp_opening_rate,
           closures / nullif(active_previous_month, 0) as exp_closure_rate,
           (openings - closures) / nullif(active_previous_month, 0) as exp_net_formation_rate
    from with_previous where has_rate
)
select * from expected
where (exp_opening_rate is null) <> (opening_rate is null) or abs(exp_opening_rate - opening_rate) > 1e-12
   or (exp_closure_rate is null) <> (closure_rate is null) or abs(exp_closure_rate - closure_rate) > 1e-12
   or (exp_net_formation_rate is null) <> (net_formation_rate is null) or abs(exp_net_formation_rate - net_formation_rate) > 1e-12
