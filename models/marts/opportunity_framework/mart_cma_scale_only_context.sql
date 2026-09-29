-- One row per scale-only CMA (the 6 Tier 3 CMAs without 0722 momentum history). Scale and composition context only:
-- no opportunity score, rank or momentum classification is produced, and nothing is imputed.
-- Percentiles here are across all 41 CMAs and are not comparable with the 35-CMA framework percentiles.
with inputs as (
    select * from {{ ref('int_opportunity__transformed_indicators') }} where component_id in ('C01_LOG_SCALE', 'C15_SHARE_20_99')
),
universe as (
    select cma_code_sgc2021, component_id, raw_value, transformed_value,
           {{ average_rank_percentile('transformed_value', 'component_id') }} as percentile_41_cma
    from inputs
),
sectors as (
    select s.cma_code_sgc2021, s.naics_sector, d.industry_label, s.share,
           row_number() over (partition by s.cma_code_sgc2021 order by s.share desc, s.naics_sector) as position
    from {{ ref('fact_cma_sector_composition_snapshot') }} as s
    join {{ ref('dim_industry') }} as d on d.industry_system = 'NAICS2022_1097' and d.industry_code = s.naics_sector
    where s.sector_role = 'business_sector'
),
unclassified as (
    select cma_code_sgc2021, share as unclassified_share_of_sme_locations
    from {{ ref('fact_cma_sector_composition_snapshot') }} where naics_sector = 'UNCLASSIFIED'
)
select
    d.cma_code_sgc2021, d.cma_label_sgc2021, d.tier as evidence_tier, d.vintage_status,
    sc.raw_value                         as sme_employer_locations,
    sc.transformed_value                 as log10_sme_employer_locations,
    sc.percentile_41_cma                 as scale_percentile_41_cma,
    fit.raw_value                        as share_20_99,
    fit.percentile_41_cma                as share_20_99_percentile_41_cma,
    sec.naics_sector                     as largest_sector_code,
    sec.industry_label                   as largest_sector_label,
    sec.share                            as largest_sector_share,
    u.unclassified_share_of_sme_locations,
    d.ised_region_key                    as context_ised_region_key,
    false                                as full_score_eligible,
    'Scale-only limited evidence'        as evidence_classification,
    'No 0722 momentum history (a census agglomeration in 2016): no opportunity score, rank or momentum classification; scale and composition context only' as evidence_limitation,
    'Percentiles are across all 41 CMAs (table 1097, December 2025) and are not comparable with the 35-CMA framework percentiles' as percentile_note,
    '1097 December 2025 (REF_DATE 2025-07)' as source_period,
    'location'                           as statistical_unit,
    'External market context from public aggregates only; not a prospect, lead or customer list' as interpretation_note
from {{ ref('dim_cma') }} as d
join universe as sc on sc.cma_code_sgc2021 = d.cma_code_sgc2021 and sc.component_id = 'C01_LOG_SCALE'
join universe as fit on fit.cma_code_sgc2021 = d.cma_code_sgc2021 and fit.component_id = 'C15_SHARE_20_99'
join sectors as sec on sec.cma_code_sgc2021 = d.cma_code_sgc2021 and sec.position = 1
join unclassified as u on u.cma_code_sgc2021 = d.cma_code_sgc2021
where not d.momentum_comparison_eligible
order by d.cma_code_sgc2021
