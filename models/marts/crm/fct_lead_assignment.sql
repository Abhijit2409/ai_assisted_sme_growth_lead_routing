-- One row per assignment (RM or queue ownership) with capacity at assignment. Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select h.*, c.utilization_ratio as rm_utilization_at_assignment, c.capacity_state as rm_capacity_state_at_assignment
from {{ ref('int_crm__lead_assignment_history') }} h
left join {{ ref('int_crm__rm_capacity_at_assignment') }} c using (assignment_id)
