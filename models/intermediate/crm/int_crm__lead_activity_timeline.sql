-- One row per activity with its lifecycle event and position in the lead's timeline (synthetic data).
-- Event mapping: attempts without two-way contact -> CONTACT_ATTEMPTED; the lead's first two-way contact -> FIRST_TWO_WAY_CONTACT;
-- nurture tasks -> NURTURE_SCHEDULED; a two-way contact while the lead is NURTURING -> NURTURE_REENGAGED; anything else -> FOLLOW_UP_ACTIVITY.
with a as (
    select a.*,
           row_number() over (partition by a.lead_id order by a.activity_at, a.activity_id)                                     as activity_sequence,
           a.activity_outcome in ('two_way_contact', 'meeting_held')                                                           as is_two_way_contact
    from {{ ref('stg_crm__lead_activity') }} a
),
status_at as (
    select a.activity_id, h.to_status as lead_status_before_activity
    from a asof join (
        select lead_id, changed_at, arg_max(to_status, status_history_id) as to_status
        from {{ ref('stg_crm__lead_status_history') }} group by 1, 2
    ) h on h.lead_id = a.lead_id and h.changed_at < a.activity_at
),
first_contact as (
    select lead_id, min(activity_sequence) as seq from a where is_two_way_contact group by 1
)
select
    a.activity_id,
    a.lead_id,
    a.assignment_id,
    a.rm_id,
    a.activity_at,
    a.activity_type,
    a.activity_outcome,
    a.counts_as_contact_attempt,
    a.next_contact_due_at,
    a.activity_sequence,
    s.lead_status_before_activity,
    case
        when a.activity_type = 'nurture_task' then 'NURTURE_SCHEDULED'
        when a.activity_sequence = f.seq then 'FIRST_TWO_WAY_CONTACT'
        when a.is_two_way_contact and s.lead_status_before_activity = 'NURTURING' then 'NURTURE_REENGAGED'
        when f.seq is null or a.activity_sequence < f.seq then 'CONTACT_ATTEMPTED'
        else 'FOLLOW_UP_ACTIVITY'
    end                                                                                         as lifecycle_event_code
from a
left join first_contact f using (lead_id)
left join status_at s using (activity_id)
