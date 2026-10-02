-- One row per SLA clock with its rule attributes (synthetic data; hypothetical SLA rules awaiting approval).
select
    e.sla_event_id,
    e.lead_id,
    e.assignment_id,
    e.sla_id,
    r.sla_name,
    r.time_basis,
    cast(r.target_value as double)                                     as target_value,
    r.target_unit,
    e.responsible_role,
    e.started_at,
    e.warning_at,
    e.target_at,
    e.stopped_at,
    e.stop_event_code,
    e.sla_outcome,
    e.sla_outcome in ('breached_closed', 'breached_open')              as is_breached,
    e.sla_outcome in ('open', 'warning_open', 'breached_open')         as is_open_at_cutoff,
    e.paused_minutes,
    e.exception_reason,
    e.elapsed_business_minutes,
    e.escalation_level,
    e.rule_version_id
from {{ ref('stg_crm__sla_event') }} e
join {{ source('crm_design', 'sla_rule_catalog') }} r on r.sla_id = e.sla_id
