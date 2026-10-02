-- One row per activity with its lifecycle event. Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select * from {{ ref('int_crm__lead_activity_timeline') }}
