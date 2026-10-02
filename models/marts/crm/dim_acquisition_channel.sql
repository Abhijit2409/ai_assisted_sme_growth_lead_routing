-- One row per acquisition channel. Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select channel_code, channel_name, default_assignment_sla_id from {{ ref('stg_crm__acquisition_channel') }}
