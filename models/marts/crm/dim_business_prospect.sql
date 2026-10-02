-- One row per synthetic business prospect (fictional names; no contact details). Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select
    p.prospect_id,
    p.synthetic_business_id,
    p.legal_name,
    p.normalized_legal_name,
    p.operating_name,
    p.employee_band,
    case when p.employee_band in ('1-4', '5-19') then 'micro' when p.employee_band in ('20-99', '100-499') then 'established'
         else 'outside SME scope or unknown' end                                                           as sme_segment,
    p.industry_system,
    p.industry_code,
    i.industry_label,
    p.country_code,
    p.existing_relationship_flag
from {{ ref('stg_crm__business_prospect') }} p
left join {{ ref('dim_industry') }} i on i.industry_system = p.industry_system and i.industry_code = p.industry_code
