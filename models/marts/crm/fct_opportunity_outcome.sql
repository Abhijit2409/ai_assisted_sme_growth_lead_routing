-- One row per opportunity (at most one per lead). Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select o.* exclude (created_at, updated_at, record_source),
       date_diff('second', o.opened_at, o.closed_at) / 86400.0 as days_open_to_close
from {{ ref('stg_crm__opportunity_outcome') }} o
