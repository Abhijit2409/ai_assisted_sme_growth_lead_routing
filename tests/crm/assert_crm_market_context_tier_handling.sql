{{ config(tags=['crm_data']) }}
-- Tier 3 leads keep their CMA, the limited-evidence flag, scale-only context and NULL score and rank; leads without a CMA carry no_market_context and NULL market fields; full-framework leads have a score.
select m.lead_id from {{ ref('int_crm__lead_market_context') }} m
where (m.evidence_tier = 3) <> (m.market_evidence_flag = 'scale_only_limited_evidence')
   or (m.evidence_tier in (1, 2)) <> (m.market_evidence_flag = 'full_framework')
   or (m.cma_code_sgc2021 is null) <> (m.market_evidence_flag = 'no_market_context')
   or (m.market_evidence_flag <> 'full_framework' and (m.market_opportunity_score is not null or m.market_opportunity_rank is not null or m.market_scenario_id is not null))
   or (m.market_evidence_flag = 'full_framework' and m.market_opportunity_score is null)
   or (m.market_evidence_flag = 'scale_only_limited_evidence' and not m.scale_only_context_available)
