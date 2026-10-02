{{ config(tags=['crm_data']) }}
-- Event timestamps lie inside the synthetic period [2025-06-01, 2026-06-01) UTC.
select 'lead' t, lead_id id from {{ ref('stg_crm__lead') }} where captured_at < timestamp '2025-06-01' or captured_at >= timestamp '2026-06-01'
union all select 'status', status_history_id from {{ ref('stg_crm__lead_status_history') }} where changed_at < timestamp '2025-06-01' or changed_at >= timestamp '2026-06-01'
union all select 'assignment', assignment_id from {{ ref('stg_crm__lead_assignment') }} where assigned_at < timestamp '2025-06-01' or coalesce(ended_at, assigned_at) >= timestamp '2026-06-01'
union all select 'activity', activity_id from {{ ref('stg_crm__lead_activity') }} where activity_at < timestamp '2025-06-01' or activity_at >= timestamp '2026-06-01'
union all select 'sla', sla_event_id from {{ ref('stg_crm__sla_event') }} where started_at < timestamp '2025-06-01' or coalesce(stopped_at, started_at) >= timestamp '2026-06-01'
union all select 'decision', routing_decision_id from {{ ref('stg_crm__routing_decision') }} where decided_at < timestamp '2025-06-01' or decided_at >= timestamp '2026-06-01'
union all select 'opportunity', opportunity_id from {{ ref('stg_crm__opportunity_outcome') }} where opened_at < timestamp '2025-06-01' or coalesce(closed_at, opened_at) >= timestamp '2026-06-01'
