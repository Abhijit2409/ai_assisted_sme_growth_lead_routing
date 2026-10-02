-- One row per synthetic campaign. Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select campaign_id, campaign_code, campaign_name, target_segment, campaign_start_date, campaign_end_date,
       date_diff('day', campaign_start_date, campaign_end_date) + 1 as campaign_days
from {{ ref('stg_crm__campaign') }}
