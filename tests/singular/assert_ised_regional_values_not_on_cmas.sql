-- ISED regional values are never attached to individual CMAs: CMA rows come only from ISED CMA rows (Tier 1 codes),
-- region rows never carry a CMA code, and CMAs without an ISED CMA row have no ISED CMA fields.
select 'CMA segment not a Tier 1 CMA' as issue, f.segment_key
from {{ ref('fact_ised_financing_context') }} as f
left join {{ ref('dim_cma') }} as d on d.cma_code_sgc2021 = f.segment_key
where f.segment_dimension = 'cma' and (d.tier is null or d.tier <> 1)
union all
select 'region segment carries a CMA code', segment_key from {{ ref('fact_ised_financing_context') }}
where segment_dimension = 'region' and segment_key in (select cma_code_sgc2021 from {{ ref('dim_cma') }})
union all
select 'non-Tier-1 CMA with ISED CMA fields', cma_code_sgc2021 from {{ ref('dim_cma') }}
where tier <> 1 and (has_ised_cma_context or ised_cma_label is not null or ised_geography_caveat is not null)
union all
select 'Quebec province row resolved to the Quebec CMA', segment_key from {{ ref('fact_ised_financing_context') }}
where segment_dimension = 'region' and segment_label_published = 'Quebec' and segment_key <> '24'
