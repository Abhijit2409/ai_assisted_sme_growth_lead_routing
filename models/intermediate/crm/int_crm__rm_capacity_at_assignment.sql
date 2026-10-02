-- One row per RM assignment with the RM's capacity snapshot available at assignment time
-- (latest snapshot with snapshot_as_of_utc <= assigned_at). Synthetic data; the 40-point capacity is a simulation setting.
select
    a.assignment_id,
    a.lead_id,
    a.rm_id,
    a.assignment_role,
    a.assigned_at,
    a.manual_override_flag,
    a.capacity_exception_flag,
    s.capacity_snapshot_id,
    s.snapshot_date,
    s.availability_status,
    s.available_capacity_points,
    s.weighted_workload_points,
    s.utilization_ratio,
    s.capacity_state
from {{ ref('stg_crm__lead_assignment') }} a
asof left join {{ ref('stg_crm__rm_capacity_snapshot') }} s
    on s.rm_id = a.rm_id and s.snapshot_as_of_utc <= a.assigned_at
where a.rm_id is not null
