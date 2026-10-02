-- One row per lead: current status from the status history (latest change), with lifecycle milestones.
-- Synthetic data; no scoring. Ties at the same timestamp resolve by status_history_id (emission order).
with h as (
    select *, row_number() over (partition by lead_id order by changed_at desc, status_history_id desc) as rn_desc
    from {{ ref('stg_crm__lead_status_history') }}
),
milestones as (
    select
        lead_id,
        min(changed_at) filter (where to_status = 'READY_FOR_ASSIGNMENT')                  as first_ready_at,
        min(changed_at) filter (where to_status = 'ASSIGNED')                              as first_assigned_at,
        min(changed_at) filter (where to_status = 'CONTACTED')                             as first_contacted_at,
        min(changed_at) filter (where to_status = 'QUALIFIED')                             as qualified_at,
        min(changed_at) filter (where to_status = 'CONVERTED')                             as converted_at,
        count(*) filter (where transition_id in ('T-28', 'T-29'))                          as audited_reopening_count,
        count(*) filter (where to_status = 'VALIDATION_PENDING')                           as validation_pending_count,
        count(*)                                                                           as status_change_count
    from {{ ref('stg_crm__lead_status_history') }}
    group by lead_id
)
select
    h.lead_id,
    h.to_status                                                   as current_lead_status,
    h.changed_at                                                  as status_changed_at,
    h.transition_id                                               as last_transition_id,
    h.disposition_code,
    s.is_terminal = 'true'                                        as is_terminal,
    m.first_ready_at,
    m.first_assigned_at,
    m.first_contacted_at,
    m.qualified_at,
    m.converted_at,
    m.audited_reopening_count,
    m.validation_pending_count,
    m.status_change_count
from h
join milestones m using (lead_id)
join {{ source('crm_design', 'lead_status_catalog') }} s on s.status_code = h.to_status
where h.rn_desc = 1
