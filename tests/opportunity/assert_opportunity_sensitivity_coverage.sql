{{ config(tags=['opportunity']) }}
-- Sensitivity outputs cover every scored CMA: one sensitivity row per CMA, 5 leave-one-out runs, 4 normalization
-- methods and both volatility treatments; the sensitivity rank ranges agree with the framework ranks.
select 'sensitivity rows differ from scored CMAs' as issue, count(*) as n from {{ ref('mart_cma_opportunity_sensitivity') }}
having count(*) <> (select count(distinct cma_code_sgc2021) from {{ ref('mart_cma_opportunity_framework') }})
union all
select 'leave-one-out incomplete', count(*) from {{ ref('int_opportunity__leave_one_out_scores') }} having count(*) <> 5 * 35
union all
select 'normalization comparison incomplete', count(*) from {{ ref('int_opportunity__scenario_scores') }}
where scenario_id = 'balanced' having count(*) <> 4 * 35
union all
select 'rank range disagrees with framework', count(*) from {{ ref('mart_cma_opportunity_sensitivity') }} as s
join (select cma_code_sgc2021, min(score_rank) mn, max(score_rank) mx from {{ ref('mart_cma_opportunity_framework') }} group by 1) as f using (cma_code_sgc2021)
where s.min_scenario_rank <> f.mn or s.max_scenario_rank <> f.mx having count(*) > 0
union all
select 'sensitivity field missing', count(*) from {{ ref('mart_cma_opportunity_sensitivity') }}
where max_leave_one_out_rank_shift is null or max_normalization_rank_shift is null or rank_unadjusted_volatility_penalty is null
   or stability_group is null or position_driver is null having count(*) > 0
