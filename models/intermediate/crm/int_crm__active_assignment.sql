-- One row per lead that currently has an active primary owner (an RM or a named queue) at the data cut-off.
-- Synthetic data. At most one active primary assignment exists per lead (tested).
select
    lead_id,
    assignment_id,
    owner_type,
    rm_id,
    queue_code,
    assignment_status,
    assigned_at,
    assignment_reason
from {{ ref('int_crm__lead_assignment_history') }}
where active_flag and assignment_role = 'primary'
