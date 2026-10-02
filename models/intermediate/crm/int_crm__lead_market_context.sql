-- One row per lead with its external market context (synthetic leads; public-data market marts).
-- Full-framework leads (Tier 1-2 CMAs) carry the configured scenario's opportunity score and rank; Tier 3 leads keep their
-- valid CMA, the limited-evidence flag and scale-only context with NULL score and rank; leads without a CMA carry
-- no_market_context and NULL market fields. Context only: never an input to validity, duplicate, eligibility, SLA,
-- capacity or compliance controls (BR-009).
select
    l.lead_id,
    l.cma_code_sgc2021,
    d.tier                                                                             as evidence_tier,
    l.market_evidence_flag,
    case when l.market_evidence_flag = 'full_framework' then '{{ var("crm_market_context_scenario") }}' end as market_scenario_id,
    f.opportunity_score                                                                as market_opportunity_score,
    f.score_rank                                                                       as market_opportunity_rank,
    f.opportunity_classification                                                       as market_opportunity_classification,
    so.cma_code_sgc2021 is not null                                                    as scale_only_context_available,
    so.scale_percentile_41_cma                                                         as scale_only_scale_percentile,
    so.evidence_classification                                                         as scale_only_evidence_classification
from {{ ref('stg_crm__lead') }} l
left join {{ ref('dim_cma') }} d on d.cma_code_sgc2021 = l.cma_code_sgc2021
left join {{ ref('mart_cma_opportunity_framework') }} f
    on f.cma_code_sgc2021 = l.cma_code_sgc2021 and f.scenario_id = '{{ var("crm_market_context_scenario") }}'
    and l.market_evidence_flag = 'full_framework'
left join {{ ref('mart_cma_scale_only_context') }} so
    on so.cma_code_sgc2021 = l.cma_code_sgc2021 and l.market_evidence_flag = 'scale_only_limited_evidence'
