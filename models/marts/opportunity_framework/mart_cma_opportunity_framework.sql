-- One row per full-framework CMA (35) x core weighting scenario (4). External market opportunity signal:
-- raw, transformed and normalized components, pillar scores, weights, contributions, score, rank, percentile,
-- classification, evidence flags and context. Context columns (prefixed context_) never enter the score.
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
core_scenarios as (
    select distinct scenario_id, scenario_name, business_interpretation from {{ ref('opportunity_weight_scenarios') }} where scenario_role = 'core'
),
weights as (
    select scenario_id,
           max(case when pillar = 'scale' then pillar_weight end)          as weight_scale,
           max(case when pillar = 'momentum' then pillar_weight end)       as weight_momentum,
           max(case when pillar = 'dynamism' then pillar_weight end)       as weight_dynamism,
           max(case when pillar = 'commercial_fit' then pillar_weight end) as weight_commercial_fit,
           max(case when pillar = 'stability' then pillar_weight end)      as weight_stability
    from {{ ref('opportunity_weight_scenarios') }} group by scenario_id
),
components as (
    select n.cma_code_sgc2021,
           max(case when component_id = 'C01_LOG_SCALE' then raw_value end)                         as c01_sme_employer_locations,
           max(case when component_id = 'C01_LOG_SCALE' then transformed_value end)                 as c01_log10_sme_employer_locations,
           max(case when component_id = 'C01_LOG_SCALE' then normalized_percentile end)             as c01_normalized,
           max(case when component_id = 'C03_OPENING_RATE' then raw_value end)                      as c03_opening_rate_12m,
           max(case when component_id = 'C03_OPENING_RATE' then normalized_percentile end)          as c03_normalized,
           max(case when component_id = 'C05_NET_FORMATION_RATE' then raw_value end)                as c05_net_formation_rate_12m,
           max(case when component_id = 'C05_NET_FORMATION_RATE' then normalized_percentile end)    as c05_normalized,
           max(case when component_id = 'C09_NET_FORMATION_TREND' then raw_value end)               as c09_net_formation_trend_per_year,
           max(case when component_id = 'C09_NET_FORMATION_TREND' then normalized_percentile end)   as c09_normalized,
           max(case when component_id = 'C15_SHARE_20_99' then raw_value end)                       as c15_share_20_99,
           max(case when component_id = 'C15_SHARE_20_99' then normalized_percentile end)           as c15_normalized,
           max(case when component_id = 'C10_VOLATILITY_SCALE_ADJUSTED' then raw_value end)         as c10_net_formation_rate_sd_12m,
           max(case when component_id = 'C10_VOLATILITY_RAW' then transformed_value end)           as c10_log10_sd,
           max(case when component_id = 'C10_VOLATILITY_SCALE_ADJUSTED' then transformed_value end) as c10_scale_adjusted_residual,
           max(case when component_id = 'C10_VOLATILITY_SCALE_ADJUSTED' then normalized_percentile end) as c10_stability_normalized,
           max(case when component_id = 'C05_NET_FORMATION_RATE' then within_pillar_weight end)     as within_momentum_weight_c05,
           max(case when component_id = 'C09_NET_FORMATION_TREND' then within_pillar_weight end)    as within_momentum_weight_c09,
           sum(case when component_id in ('C03_OPENING_RATE', 'C05_NET_FORMATION_RATE', 'C09_NET_FORMATION_TREND')
                    then e_flagged_observations end)                                                as momentum_e_flagged_observations,
           min(case when component_id in ('C03_OPENING_RATE', 'C05_NET_FORMATION_RATE') then usable_observations end) as recent_usable_months,
           max(case when component_id = 'C09_NET_FORMATION_TREND' then usable_observations end)     as trend_usable_months
    from {{ ref('int_opportunity__normalized_indicators') }} as n
    group by n.cma_code_sgc2021
),
pillars as (
    select cma_code_sgc2021,
           max(case when pillar = 'scale' then pillar_score end)          as pillar_scale,
           max(case when pillar = 'momentum' then pillar_score end)       as pillar_momentum,
           max(case when pillar = 'dynamism' then pillar_score end)       as pillar_dynamism,
           max(case when pillar = 'commercial_fit' then pillar_score end) as pillar_commercial_fit,
           max(case when pillar = 'stability' then pillar_score end)      as pillar_stability
    from {{ ref('int_opportunity__pillar_scores') }}
    where normalization_method = (select primary_normalization from thresholds)
    group by cma_code_sgc2021
),
scores as (
    select * from {{ ref('int_opportunity__scenario_scores') }}
    where scenario_role = 'core' and normalization_method = (select primary_normalization from thresholds)
),
ised as (
    select segment_key as cma_code_sgc2021, value_usable as context_ised_external_financing_request_rate_pct,
           quality_code as context_ised_quality_code, mvp_use_status as context_ised_use_status
    from {{ ref('fact_ised_financing_context') }}
    where measure_id = 'ISED_T01_M001' and segment_dimension = 'cma'
),
assembled as (
    select
        d.cma_code_sgc2021, d.cma_label_sgc2021, d.tier as evidence_tier, d.evidence_label,
        s.scenario_id, cs.scenario_name, cs.business_interpretation as scenario_interpretation,
        c.*  exclude (cma_code_sgc2021),
        p.* exclude (cma_code_sgc2021),
        w.* exclude (scenario_id),
        w.weight_scale * p.pillar_scale                                   as contribution_scale,
        w.weight_momentum * p.pillar_momentum                             as contribution_momentum,
        w.weight_dynamism * p.pillar_dynamism                             as contribution_dynamism,
        w.weight_commercial_fit * p.pillar_commercial_fit                 as contribution_commercial_fit,
        w.weight_stability * p.pillar_stability                           as contribution_stability,
        s.opportunity_score, s.score_rank, s.score_position, s.tied_markets, s.score_percentile,
        d.boundary_change_flag, d.has_ised_cma_context,
        i.context_ised_external_financing_request_rate_pct, i.context_ised_quality_code, i.context_ised_use_status
    from scores as s
    join {{ ref('dim_cma') }} as d using (cma_code_sgc2021)
    join core_scenarios as cs using (scenario_id)
    join weights as w using (scenario_id)
    join components as c using (cma_code_sgc2021)
    join pillars as p using (cma_code_sgc2021)
    left join ised as i using (cma_code_sgc2021)
),
dominant as (
    -- pillar with the largest weighted contribution; ties resolved in pillar order (scale, momentum, dynamism, fit, stability)
    select cma_code_sgc2021, scenario_id,
           case greatest(contribution_scale, contribution_momentum, contribution_dynamism, contribution_commercial_fit, contribution_stability)
               when contribution_scale then 'scale' when contribution_momentum then 'momentum' when contribution_dynamism then 'dynamism'
               when contribution_commercial_fit then 'commercial_fit' else 'stability' end as dominant_pillar
    from assembled
)
select
    a.*,
    dm.dominant_pillar,
    case
        when a.score_percentile >= t.top_quartile_percentile then 'Stronger external opportunity signal'
        when a.pillar_scale >= t.lead_pillar_threshold and a.pillar_momentum < t.other_pillar_ceiling then 'Scale-led external opportunity signal'
        when a.pillar_momentum >= t.lead_pillar_threshold and a.pillar_scale < t.other_pillar_ceiling then 'Momentum-led external opportunity signal'
        when a.score_percentile >= t.bottom_quartile_percentile then 'Moderate external opportunity signal'
        else 'Weaker current external signal (monitor)'
    end                                                                   as opportunity_classification,
    case when abs(a.c05_net_formation_rate_12m) > t.momentum_clarity_multiple * a.c10_net_formation_rate_sd_12m / sqrt(12)
         then case when a.c05_net_formation_rate_12m > 0 then 'clear positive' else 'clear negative' end
         else 'within month-to-month noise' end                          as momentum_signal_clarity,
    t.primary_normalization                                               as normalization_method,
    'full framework: 35 CMAs with complete 0722 momentum history'         as framework_population,
    '0-100 relative index within the 35-CMA population (not a probability, revenue, lead volume or conversion estimate)' as score_scale,
    'External market signal from public aggregates only; not a prospect, lead or customer list, and not an investment decision' as interpretation_note,
    'fact_cma_market_scale_snapshot; fact_cma_size_composition_snapshot; fact_cma_momentum_monthly; context: fact_ised_financing_context' as source_models,
    '1097 December 2025 (REF_DATE 2025-07); 0722 2025-06 to 2026-05 (12 months) and 2023-06 to 2026-05 (36 months); ISED context 2023' as source_periods
from assembled as a
join dominant as dm using (cma_code_sgc2021, scenario_id)
cross join thresholds as t
order by scenario_id, score_position
