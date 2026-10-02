-- One row per hypothetical sales territory keyed on SGC 2021 geography. Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select t.territory_id, t.territory_code, t.territory_type, t.cma_code_sgc2021, c.cma_label_sgc2021, t.province_code, t.sales_region, t.evidence_tier
from {{ ref('stg_crm__territory') }} t
left join {{ ref('dim_cma') }} c using (cma_code_sgc2021)
