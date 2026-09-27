-- Reporting spine: one row per SGC 2021 CMA (41) with evidence tier, vintage and ISED-context flags.
select
    cma_code_sgc2021,
    cma_label_sgc2021,
    dguid_2021,
    province_code,
    cma_code_sgc2016,
    dguid_2016,
    mapping_status                                          as vintage_status,
    boundary_change_flag,
    preliminary_csd_additions,
    tier,
    tier in (1, 2)                                          as in_primary_decision_set,
    tier in (1, 2)                                          as momentum_comparison_eligible,
    has_momentum_0722,
    has_scale_1097,
    has_ised_cma_context,
    ised_cma_label,
    ised_region_key,
    ised_geography_caveat,
    case tier when 1 then 'momentum + scale + ISED CMA context'
              when 2 then 'momentum + scale; ISED regional/national context only'
              when 3 then 'scale only (no 0722 momentum); ISED regional context only' end as evidence_label,
    case tier
        when 1 then 'Momentum (0722 enterprises, monthly to 2026-05) + scale and composition (1097 locations, December 2025) + ISED CMA context (2023 survey estimates)'
        when 2 then 'Momentum (0722 enterprises, monthly to 2026-05) + scale and composition (1097 locations, December 2025); ISED at region or national level only, no CMA estimate'
        when 3 then 'Scale and composition only (1097 locations, December 2025); a CA in 2016, so no 0722 momentum series; never compared on momentum with Tiers 1-2'
    end                                                     as evidence_coverage_description
from {{ ref('int_market__cma_evidence_coverage') }}
order by cma_code_sgc2021
