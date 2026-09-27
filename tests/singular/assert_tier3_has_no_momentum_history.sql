-- Tier 3 CMAs (CAs in 2016) have no 0722 history: no momentum row, no momentum flag, no comparison eligibility.
select 'momentum row for a Tier 3 CMA' as issue, f.cma_code_sgc2021, count(*) as rows_failing
from {{ ref('fact_cma_momentum_monthly') }} as f
join {{ ref('dim_cma') }} as d using (cma_code_sgc2021)
where d.tier = 3
group by all
union all
select 'Tier 3 CMA flagged with momentum or eligibility', cma_code_sgc2021, 1
from {{ ref('dim_cma') }}
where tier = 3 and (has_momentum_0722 or momentum_comparison_eligible or in_primary_decision_set or cma_code_sgc2016 is not null)
