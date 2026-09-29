-- Grain: one row per full-framework CMA x normalization method x pillar.
-- Pillar score = weighted mean of the pillar's normalized components (within-pillar weights sum to 1), so a pillar with
-- two correlated components (momentum: C05 and C09) never gets extra influence from its component count.
with components as (
    select cma_code_sgc2021, component_id, pillar, within_pillar_weight,
           normalized_percentile, normalized_min_max, normalized_min_max_winsorized, normalized_robust_z
    from {{ ref('int_opportunity__normalized_indicators') }}
),
normalized as (
    unpivot components
    on normalized_percentile, normalized_min_max, normalized_min_max_winsorized, normalized_robust_z
    into name normalization_column value normalized_value
)
select
    cma_code_sgc2021,
    case normalization_column
        when 'normalized_percentile' then 'percentile_rank_average_ties'
        when 'normalized_min_max' then 'min_max'
        when 'normalized_min_max_winsorized' then 'min_max_tukey_capped'
        when 'normalized_robust_z' then 'robust_z_clipped'
    end                                                              as normalization_method,
    pillar,
    sum(within_pillar_weight * normalized_value order by component_id) / sum(within_pillar_weight order by component_id) as pillar_score,
    count(*)                                                         as components_in_pillar,
    string_agg(component_id, ';' order by component_id)              as pillar_components
from normalized
group by cma_code_sgc2021, normalization_column, pillar
