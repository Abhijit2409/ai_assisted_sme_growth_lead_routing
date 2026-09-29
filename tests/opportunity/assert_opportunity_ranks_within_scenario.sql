{{ config(tags=['opportunity']) }}
-- Ranks are computed within each scenario: rank = 1 + number of markets with a strictly higher (rounded) score, tied
-- markets share a rank and are flagged, and positions 1-35 are unique and break ties by CMA code.
with f as (select *, round(opportunity_score, 9) as s from {{ ref('mart_cma_opportunity_framework') }})
select 'rank not competition rank within scenario' as issue, a.scenario_id, a.cma_code_sgc2021
from f as a
where a.score_rank <> 1 + (select count(*) from f as b where b.scenario_id = a.scenario_id and b.s > a.s)
   or a.tied_markets <> (select count(*) from f as b where b.scenario_id = a.scenario_id and b.s = a.s)
union all
select 'positions not 1..35', scenario_id, null from f group by scenario_id
having count(distinct score_position) <> 35 or min(score_position) <> 1 or max(score_position) <> 35
union all
select 'tie not broken by CMA code', a.scenario_id, a.cma_code_sgc2021 from f as a join f as b
  on a.scenario_id = b.scenario_id and a.s = b.s and a.cma_code_sgc2021 < b.cma_code_sgc2021
where a.score_position > b.score_position
union all
select 'percentile inconsistent with rank', a.scenario_id, a.cma_code_sgc2021 from f as a join f as b
  on a.scenario_id = b.scenario_id and a.s > b.s
where a.score_percentile <= b.score_percentile
