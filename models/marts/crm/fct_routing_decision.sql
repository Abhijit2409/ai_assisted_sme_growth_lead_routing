-- One row per historical routing decision (simulated hypothetical behaviour; not a recommendation). Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select * exclude (created_at, updated_at, record_source) from {{ ref('stg_crm__routing_decision') }}
