-- Decision population for the lead-prioritization and routing framework.
-- Grain: one row per lead per routing-decision timestamp (decision_id is unique).
-- Two triggers:
--   historical_routing_decision  - every timestamp in the synthetic routing log (initial, reassignment, override, queue_release).
--                                  Only the decision identifier, lead and timestamp are taken from that log; no decision outcome,
--                                  selected RM, match flag, candidate count or rule trace is ever read.
--   intake_routability_review    - leads that never reached the routing engine, evaluated at their first status change away
--                                  from NEW. Without these the population would silently exclude duplicates, invalid, merged,
--                                  withdrawn and held leads, and the eligibility taxonomy could never be exercised.
-- As-of rule: lead_status_at_decision is recomputed from the status history using changes strictly before as_of_timestamp,
-- plus changes stamped exactly at as_of_timestamp whose event is not ASSIGNMENT_CREATED (that event is caused by the decision).
-- Hypothetical framework, fictional bank, entirely synthetic data.
with historical as (
    select
        'DEC-' || d.routing_decision_id   as decision_id,
        d.routing_decision_id,
        d.lead_id,
        d.decided_at                      as as_of_timestamp,
        'historical_routing_decision'     as decision_trigger,
        d.decision_type
    from {{ ref('stg_crm__routing_decision') }} d
),

leads_without_decision as (
    select l.lead_id, l.captured_at
    from {{ ref('stg_crm__lead') }} l
    where not exists (select 1 from {{ ref('stg_crm__routing_decision') }} d where d.lead_id = l.lead_id)
),

intake_review as (
    select
        'DEC-INTAKE-' || n.lead_id                     as decision_id,
        cast(null as varchar)                          as routing_decision_id,
        n.lead_id,
        coalesce(
            (select min(h.changed_at)
             from {{ ref('stg_crm__lead_status_history') }} h
             where h.lead_id = n.lead_id and h.to_status <> 'NEW'),
            n.captured_at)                             as as_of_timestamp,
        'intake_routability_review'                    as decision_trigger,
        'intake_review'                                as decision_type
    from leads_without_decision n
),

combined as (
    select * from historical
    union all
    select * from intake_review
)

select
    c.decision_id,
    c.routing_decision_id,
    c.lead_id,
    c.as_of_timestamp,
    cast(c.as_of_timestamp as date)                    as as_of_date,
    c.decision_trigger,
    c.decision_type,
    coalesce(
        (select h.to_status
         from {{ ref('stg_crm__lead_status_history') }} h
         where h.lead_id = c.lead_id
           and (h.changed_at < c.as_of_timestamp
                or (h.changed_at = c.as_of_timestamp and h.event_code <> 'ASSIGNMENT_CREATED'))
         order by h.changed_at desc, h.status_history_id desc
         limit 1),
        'NEW')                                         as lead_status_at_decision,
    row_number() over (partition by c.lead_id order by c.as_of_timestamp, c.decision_id)
                                                       as decision_sequence_for_lead,
    cast(c.as_of_timestamp as date) < (select cast(value as date) from {{ ref('routing_evaluation_thresholds') }}
                                       where parameter = 'warm_up_period_end_exclusive')
                                                       as is_capacity_warm_up_period,
    '{{ var("lead_routing_rule_version") }}'           as rule_version
from combined c
