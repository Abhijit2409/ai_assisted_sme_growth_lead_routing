-- One row per assignment (RM or queue ownership) with its position in the lead's ownership history.
-- Synthetic data. Queue ownership (queue_code, no RM) stays distinguishable from RM ownership.
select
    a.assignment_id,
    a.lead_id,
    a.assignment_role,
    case when a.rm_id is null then 'queue' else 'relationship_manager' end                                    as owner_type,
    a.rm_id,
    a.queue_code,
    a.assignment_status,
    a.active_flag,
    a.assignment_reason,
    a.assigned_at,
    a.acknowledged_at,
    a.ended_at,
    a.prior_assignment_id,
    a.prior_rm_id,
    a.reassignment_reason,
    a.manual_override_flag,
    a.override_reason_code,
    a.override_user_role,
    a.capacity_exception_flag,
    a.routing_decision_id,
    a.rule_version_id,
    row_number() over (partition by a.lead_id, a.assignment_role order by a.assigned_at, a.assignment_id)       as ownership_sequence,
    a.prior_assignment_id is not null                                                                           as is_reassignment,
    date_diff('second', a.assigned_at, coalesce(a.ended_at, timestamp '2026-06-01 00:00:00')) / 3600.0          as hours_owned_to_end_or_cutoff
from {{ ref('stg_crm__lead_assignment') }} a
