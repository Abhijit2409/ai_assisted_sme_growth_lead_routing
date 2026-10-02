-- One row per SLA clock instance per lead or assignment (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(sla_event_id as varchar)            as sla_event_id,
    cast(lead_id as varchar)                 as lead_id,
    cast(assignment_id as varchar)           as assignment_id,
    cast(sla_id as varchar)                  as sla_id,
    cast(started_at as timestamp)            as started_at,
    cast(target_at as timestamp)             as target_at,
    cast(warning_at as timestamp)            as warning_at,
    cast(stopped_at as timestamp)            as stopped_at,
    cast(stop_event_code as varchar)         as stop_event_code,
    cast(sla_outcome as varchar)             as sla_outcome,
    cast(paused_minutes as bigint)           as paused_minutes,
    cast(escalation_level as bigint)         as escalation_level,
    cast(rule_version_id as varchar)         as rule_version_id,
    cast(responsible_role as varchar)        as responsible_role,
    cast(elapsed_business_minutes as bigint) as elapsed_business_minutes,
    cast(exception_reason as varchar)        as exception_reason,
    cast(created_at as timestamp)            as created_at,
    cast(updated_at as timestamp)            as updated_at,
    cast(record_source as varchar)           as record_source
from {{ source('synthetic_crm', 'crm_sla_event') }}
