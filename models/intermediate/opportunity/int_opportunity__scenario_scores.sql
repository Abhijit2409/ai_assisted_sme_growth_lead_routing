-- Grain: one row per full-framework CMA x weighting scenario (core and sensitivity-only) x normalization method.
-- Score = sum over pillars of pillar weight x pillar score (0-100), rounded to 1e-9: DuckDB adds the pillar terms in a
-- thread-dependent order, so without rounding mathematically equal scores could differ by ~1e-15 between builds and
-- flip ties (and every rank statistic built on them). Rounding makes scores, ties and ranks identical across builds. Rank 1 = highest score; tied scores share the
-- same (competition) rank, and score_position breaks ties by CMA code so that ordering is deterministic.
with scored as (
    select p.cma_code_sgc2021, w.scenario_id, w.scenario_role, p.normalization_method,
           round(sum(w.pillar_weight * p.pillar_score order by p.pillar), 9) as opportunity_score,
           sum(w.pillar_weight order by p.pillar) as weight_total
    from {{ ref('int_opportunity__pillar_scores') }} as p
    join {{ ref('opportunity_weight_scenarios') }} as w on w.pillar = p.pillar
    group by all
)
-- Ranking uses the score rounded to 1e-9, so that mathematically equal scores (percentiles are multiples of 1/34 and
-- weights are simple fractions) tie exactly instead of depending on floating-point summation order.
, rounded as (
    select *, round(opportunity_score, 9) as score_for_ranking from scored
)
select
    *,
    rank() over (partition by scenario_id, normalization_method order by score_for_ranking desc)                        as score_rank,
    row_number() over (partition by scenario_id, normalization_method order by score_for_ranking desc, cma_code_sgc2021) as score_position,
    count(*) over (partition by scenario_id, normalization_method, score_for_ranking)                                   as tied_markets,
    {{ average_rank_percentile('score_for_ranking', 'scenario_id, normalization_method') }}                              as score_percentile
from rounded
