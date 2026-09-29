{{ config(tags=['opportunity']) }}
-- ISED and other context columns stay outside the score: the ISED context comes only from ISED CMA rows (Tier 1),
-- is absent for other CMAs, and no ISED model feeds any opportunity scoring model.
select 'ISED context on a non-Tier-1 CMA' as issue, cma_code_sgc2021 from {{ ref('mart_cma_opportunity_framework') }}
where evidence_tier <> 1 and (context_ised_external_financing_request_rate_pct is not null or context_ised_quality_code is not null)
union all
select 'ISED context differs from the ISED CMA estimate', f.cma_code_sgc2021 from {{ ref('mart_cma_opportunity_framework') }} as f
join {{ ref('fact_ised_financing_context') }} as i
  on i.segment_key = f.cma_code_sgc2021 and i.segment_dimension = 'cma' and i.measure_id = 'ISED_T01_M001'
where f.context_ised_external_financing_request_rate_pct is distinct from i.value_usable
