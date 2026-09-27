-- Where openings and closures are both available, net formation must equal openings - closures exactly
-- (whole-number enterprise counts, so no tolerance), and it must be unavailable otherwise.
with f as (select * from {{ ref('fact_cma_momentum_monthly') }}),
pivoted as (
    select cma_code_sgc2016, ref_month, size_band_key, industry_code_0722,
           max(case when measure = 'openings' then value end) as openings,
           max(case when measure = 'closures' then value end) as closures,
           max(case when measure = 'net_formation' then value end) as net_formation,
           bool_or(measure = 'net_formation') as has_net_formation
    from f group by all
)
select * from pivoted
where has_net_formation
  and ((openings is not null and closures is not null and net_formation is distinct from openings - closures)
       or ((openings is null or closures is null) and net_formation is not null))
