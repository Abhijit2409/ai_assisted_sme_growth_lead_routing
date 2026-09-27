-- All-size momentum is all-employer context; the 1-4 and 5-19 bands are small-business measures (not an SME
-- total); 500+ is outside SME scope; every row keeps the pandemic flag for 2020-03..2021-12.
select 'mislabelled scope' as issue, size_band_key, scope_class, count(*) as rows_failing
from {{ ref('fact_cma_momentum_monthly') }}
where scope_class <> case size_band_key when 'TOTAL' then 'all-employer-business context'
                                        when '1-4' then 'small-business measure' when '5-19' then 'small-business measure'
                                        when '500-plus' then 'outside SME scope (500+)' else 'SME-specific measure' end
group by all
union all
select 'pandemic flag wrong', null, null, count(*) from {{ ref('fact_cma_momentum_monthly') }}
where pandemic_window_flag <> (ref_month between '{{ var("pandemic_window_start") }}' and '{{ var("pandemic_window_end") }}')
having count(*) > 0
union all
select 'momentum industry outside the 19-member scope', null, industry_code_0722, count(*)
from {{ ref('fact_cma_momentum_monthly') }}
where cast(industry_code_0722 as integer) not in (select industry_member_id from {{ ref('int_market__momentum_scope_members') }})
group by all
