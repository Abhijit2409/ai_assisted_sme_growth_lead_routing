{{ config(tags=['opportunity']) }}
-- Scenario weights are nonnegative and sum to 1 (tolerance 1e-9, for decimal representation only);
-- within-pillar component weights are positive and sum to 1 for every pillar.
select 'scenario weights do not sum to 1' as issue, scenario_id as detail, sum(pillar_weight) as value
from {{ ref('opportunity_weight_scenarios') }} group by scenario_id having abs(sum(pillar_weight) - 1) > 1e-9
union all
select 'negative pillar weight', scenario_id || ':' || pillar, pillar_weight from {{ ref('opportunity_weight_scenarios') }} where pillar_weight < 0
union all
select 'within-pillar weights do not sum to 1', pillar, sum(within_pillar_weight) from {{ ref('opportunity_indicator_registry') }}
group by pillar having abs(sum(within_pillar_weight) - 1) > 1e-9
union all
select 'non-positive within-pillar weight', component_id, within_pillar_weight from {{ ref('opportunity_indicator_registry') }} where within_pillar_weight <= 0
union all
select 'published weights differ from the configuration', f.scenario_id, f.weight_scale from {{ ref('mart_cma_opportunity_framework') }} as f
join (select scenario_id, max(case when pillar = 'scale' then pillar_weight end) as ws, max(case when pillar = 'momentum' then pillar_weight end) as wm,
             max(case when pillar = 'dynamism' then pillar_weight end) as wd, max(case when pillar = 'commercial_fit' then pillar_weight end) as wf,
             max(case when pillar = 'stability' then pillar_weight end) as wst
      from {{ ref('opportunity_weight_scenarios') }} group by scenario_id) as w using (scenario_id)
where f.weight_scale <> w.ws or f.weight_momentum <> w.wm or f.weight_dynamism <> w.wd or f.weight_commercial_fit <> w.wf or f.weight_stability <> w.wst
