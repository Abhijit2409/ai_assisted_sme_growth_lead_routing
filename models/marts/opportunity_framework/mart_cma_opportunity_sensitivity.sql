-- One row per full-framework CMA (35): how its position moves across the four core scenarios, four normalization methods,
-- leave-one-indicator-out and the two volatility treatments.
with thresholds as (
    select
        max(case when parameter = 'primary_normalization' then value end)                        as primary_normalization,
        max(case when parameter = 'top_quartile_percentile' then cast(value as double) end)      as top_quartile_percentile,
        max(case when parameter = 'bottom_quartile_percentile' then cast(value as double) end)   as bottom_quartile_percentile,
        max(case when parameter = 'lead_pillar_threshold' then cast(value as double) end)        as lead_pillar_threshold,
        max(case when parameter = 'other_pillar_ceiling' then cast(value as double) end)         as other_pillar_ceiling,
        max(case when parameter = 'driver_gap' then cast(value as double) end)                   as driver_gap,
        max(case when parameter = 'rank_sensitivity_threshold' then cast(value as double) end)   as rank_sensitivity_threshold,
        max(case when parameter = 'momentum_clarity_multiple' then cast(value as double) end)    as momentum_clarity_multiple
    from {{ ref('opportunity_decision_thresholds') }}
),
framework as (
    select * from {{ ref('mart_cma_opportunity_framework') }}
),
balanced as (
    select * from framework where scenario_id = 'balanced'
),
across_scenarios as (
    select cma_code_sgc2021,
           min(score_rank) as min_scenario_rank, max(score_rank) as max_scenario_rank, avg(score_rank) as mean_scenario_rank,
           stddev_pop(score_rank) as rank_standard_deviation,
           min(opportunity_score) as min_scenario_score, max(opportunity_score) as max_scenario_score,
           count(*) filter (where score_percentile >= (select top_quartile_percentile from thresholds)) as top_quartile_scenarios,
           count(*) filter (where score_percentile < (select bottom_quartile_percentile from thresholds)) as bottom_quartile_scenarios,
           count(*) as core_scenarios,
           count(distinct opportunity_classification) as distinct_classifications
    from framework group by cma_code_sgc2021
),
consistency as (
    select f.cma_code_sgc2021, avg(case when f.opportunity_classification = b.opportunity_classification then 1.0 else 0.0 end) as classification_consistency
    from framework as f join balanced as b using (cma_code_sgc2021)
    group by f.cma_code_sgc2021
),
loo_shifts as (
    select l.cma_code_sgc2021, l.left_out_component, l.score_rank - b.score_rank as rank_shift,
           row_number() over (partition by l.cma_code_sgc2021 order by abs(l.score_rank - b.score_rank) desc, l.left_out_component) as position
    from {{ ref('int_opportunity__leave_one_out_scores') }} as l
    join balanced as b using (cma_code_sgc2021)
),
loo as (
    select l.cma_code_sgc2021,
           max(abs(l.score_rank - b.score_rank)) as max_leave_one_out_rank_shift,
           max(case when x.position = 1 then x.left_out_component end) as most_influential_component,
           max(case when l.left_out_component = 'C01_LOG_SCALE' then l.score_rank - b.score_rank end)           as rank_shift_without_c01,
           max(case when l.left_out_component = 'C03_OPENING_RATE' then l.score_rank - b.score_rank end)        as rank_shift_without_c03,
           max(case when l.left_out_component = 'C05_NET_FORMATION_RATE' then l.score_rank - b.score_rank end)  as rank_shift_without_c05,
           max(case when l.left_out_component = 'C09_NET_FORMATION_TREND' then l.score_rank - b.score_rank end) as rank_shift_without_c09,
           max(case when l.left_out_component = 'C15_SHARE_20_99' then l.score_rank - b.score_rank end)         as rank_shift_without_c15
    from {{ ref('int_opportunity__leave_one_out_scores') }} as l
    join balanced as b using (cma_code_sgc2021)
    join loo_shifts as x on x.cma_code_sgc2021 = l.cma_code_sgc2021 and x.left_out_component = l.left_out_component
    group by l.cma_code_sgc2021
),
normalization as (
    select s.cma_code_sgc2021,
           max(case when s.normalization_method = 'min_max' then s.score_rank end)              as balanced_rank_min_max,
           max(case when s.normalization_method = 'min_max_tukey_capped' then s.score_rank end) as balanced_rank_min_max_tukey_capped,
           max(case when s.normalization_method = 'robust_z_clipped' then s.score_rank end)     as balanced_rank_robust_z_clipped,
           max(abs(s.score_rank - b.score_rank))                                                 as max_normalization_rank_shift
    from {{ ref('int_opportunity__scenario_scores') }} as s
    join balanced as b using (cma_code_sgc2021)
    where s.scenario_id = 'balanced'
    group by s.cma_code_sgc2021
),
volatility as (
    select cma_code_sgc2021,
           max(case when scenario_id = 'stability_aware' then score_rank end)               as rank_scale_adjusted_volatility,
           max(case when scenario_id = 'volatility_penalty_unadjusted' then score_rank end) as rank_unadjusted_volatility_penalty
    from {{ ref('int_opportunity__scenario_scores') }}
    where normalization_method = (select primary_normalization from thresholds)
      and scenario_id in ('stability_aware', 'volatility_penalty_unadjusted')
    group by cma_code_sgc2021
)
select
    b.cma_code_sgc2021, b.cma_label_sgc2021, b.evidence_tier,
    b.opportunity_score as balanced_score, b.score_rank as balanced_rank, b.opportunity_classification as balanced_classification,
    a.min_scenario_rank, a.max_scenario_rank, a.max_scenario_rank - a.min_scenario_rank as rank_range, a.mean_scenario_rank,
    a.rank_standard_deviation, a.max_scenario_score - a.min_scenario_score as score_range,
    a.top_quartile_scenarios, a.top_quartile_scenarios / a.core_scenarios::double as top_quartile_frequency,
    a.bottom_quartile_scenarios, c.classification_consistency, a.distinct_classifications,
    l.* exclude (cma_code_sgc2021),
    n.* exclude (cma_code_sgc2021),
    v.rank_scale_adjusted_volatility, v.rank_unadjusted_volatility_penalty,
    v.rank_unadjusted_volatility_penalty - v.rank_scale_adjusted_volatility as rank_shift_unadjusted_vs_adjusted_volatility,
    b.pillar_scale, b.pillar_momentum,
    case when b.pillar_scale - b.pillar_momentum >= t.driver_gap then 'scale-driven'
         when b.pillar_momentum - b.pillar_scale >= t.driver_gap then 'momentum-driven'
         else 'mixed scale and momentum' end                                               as position_driver,
    (a.max_scenario_rank - a.min_scenario_rank) >= t.rank_sensitivity_threshold
        or a.top_quartile_scenarios between 1 and a.core_scenarios - 1                      as weighting_sensitive,
    l.max_leave_one_out_rank_shift >= t.rank_sensitivity_threshold                          as indicator_dependent,
    n.max_normalization_rank_shift >= t.rank_sensitivity_threshold                          as normalization_sensitive,
    case
        when a.top_quartile_scenarios = a.core_scenarios then 'Stable stronger signal'
        when (a.max_scenario_rank - a.min_scenario_rank) >= t.rank_sensitivity_threshold
             or a.top_quartile_scenarios between 1 and a.core_scenarios - 1 then 'Assumption-sensitive'
        when a.bottom_quartile_scenarios = a.core_scenarios then 'Stable weaker signal'
        else 'Stable middle signal'
    end                                                                                    as stability_group,
    b.momentum_signal_clarity,
    'Rank 1 = strongest external signal among 35 CMAs; sensitivity describes assumption dependence, not statistical confidence' as interpretation_note
from balanced as b
join across_scenarios as a using (cma_code_sgc2021)
join consistency as c using (cma_code_sgc2021)
join loo as l using (cma_code_sgc2021)
join normalization as n using (cma_code_sgc2021)
join volatility as v using (cma_code_sgc2021)
cross join thresholds as t
order by b.score_rank, b.cma_code_sgc2021
