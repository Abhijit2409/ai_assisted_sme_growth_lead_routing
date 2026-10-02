-- One row per synthetic RM with display label, region and specializations. Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select
    r.rm_id,
    'Synthetic RM ' || right(r.rm_id, 4)                                              as rm_display_name,
    r.rm_code,
    r.rm_segment,
    r.home_territory_id,
    t.sales_region                                                                    as home_sales_region,
    t.territory_type                                                                  as home_territory_type,
    r.supported_service_languages,
    r.base_capacity_points,
    r.employment_start_date,
    r.employment_end_date,
    r.active_flag,
    max(s.industry_code) filter (where s.specialization_role = 'primary')            as primary_specialization_industry_code,
    max(s.industry_code) filter (where s.specialization_role = 'secondary')          as secondary_specialization_industry_code,
    case when max(s.industry_code) filter (where s.specialization_role = 'primary') is not null then 'dedicated_specialist' else 'generalist' end as rm_role
from {{ ref('stg_crm__relationship_manager') }} r
left join {{ ref('stg_crm__territory') }} t on t.territory_id = r.home_territory_id
left join {{ ref('stg_crm__rm_specialization') }} s on s.rm_id = r.rm_id
group by all
