{{ config(tags=['opportunity']) }}
-- Full scores exist only for the 35 CMAs with complete 0722 momentum history; the scale-only mart holds exactly the
-- Tier 3 CMAs, which carry no score, rank or momentum field.
with eligible as (select cma_code_sgc2021 from {{ ref('dim_cma') }} where momentum_comparison_eligible),
tier3 as (select cma_code_sgc2021 from {{ ref('dim_cma') }} where not momentum_comparison_eligible)
select 'framework CMA not eligible' as issue, cma_code_sgc2021 from {{ ref('mart_cma_opportunity_framework') }} where cma_code_sgc2021 not in (select * from eligible)
union all
select 'eligible CMA missing from a core scenario', e.cma_code_sgc2021 from eligible as e
cross join (select distinct scenario_id from {{ ref('opportunity_weight_scenarios') }} where scenario_role = 'core') as s
where not exists (select 1 from {{ ref('mart_cma_opportunity_framework') }} f where f.cma_code_sgc2021 = e.cma_code_sgc2021 and f.scenario_id = s.scenario_id)
union all
select 'eligible population is not 35 CMAs', cast(count(*) as varchar) from eligible having count(*) <> 35
union all
select 'scale-only CMA is not a Tier 3 CMA', cma_code_sgc2021 from {{ ref('mart_cma_scale_only_context') }}
where cma_code_sgc2021 not in (select * from tier3) or full_score_eligible
union all
select 'Tier 3 CMA missing from the scale-only view', cma_code_sgc2021 from tier3
where cma_code_sgc2021 not in (select cma_code_sgc2021 from {{ ref('mart_cma_scale_only_context') }})
union all
select 'scale-only view has a score, rank or momentum column', column_name from information_schema.columns
where table_schema = 'analytics' and table_name = 'mart_cma_scale_only_context'
  -- the required flag full_score_eligible (always false) is the only permitted column naming a score
  and ((column_name like '%score%' and column_name <> 'full_score_eligible') or column_name like '%rank%' or column_name like '%momentum%'
       or column_name like 'pillar%' or column_name like 'c03%' or column_name like 'c05%' or column_name like 'c09%' or column_name like 'c10%')
