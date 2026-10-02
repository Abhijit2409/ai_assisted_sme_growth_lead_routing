{{ config(tags=['crm_data']) }}
-- SLA clocks are ordered, stop only on their rule's stop events, and assignment-level clocks reference an assignment.
select e.sla_event_id from {{ ref('stg_crm__sla_event') }} e
join {{ source('crm_design', 'sla_rule_catalog') }} r on r.sla_id = e.sla_id
where e.warning_at < e.started_at or e.target_at < e.warning_at or e.stopped_at < e.started_at
   or (e.stopped_at is not null and not list_contains(string_split(r.stop_events, ';'), e.stop_event_code))
   or (e.stopped_at is null) <> (e.stop_event_code is null)
   or (regexp_matches(e.sla_id, '^SLA-0[5-9]') and e.assignment_id is null)
   or e.responsible_role <> r.responsible_role
