{{ config(tags=['lead_routing']) }}
-- Required test: no future features. Every event-derived feature uses only pre-decision evidence.
select 'activity counted at or after the decision' as issue, f.decision_id as id
from {{ ref('int_lead_routing__priority_features_as_of') }} f
where f.activities_before_decision <> (
    select count(*) from {{ ref('stg_crm__lead_activity') }} a
    where a.lead_id = f.lead_id and a.activity_at < f.as_of_timestamp)
union all
select 'contact attempts counted at or after the decision', f.decision_id
from {{ ref('int_lead_routing__priority_features_as_of') }} f
where f.contact_attempts_before_decision <> (
    select count(*) from {{ ref('stg_crm__lead_activity') }} a
    where a.lead_id = f.lead_id and a.activity_at < f.as_of_timestamp and a.counts_as_contact_attempt)
union all
select 'sla clocks in flight counted wrongly', f.decision_id
from {{ ref('int_lead_routing__priority_features_as_of') }} f
where f.sla_clocks_in_flight <> (
    select count(*) from {{ ref('stg_crm__sla_event') }} s
    where s.lead_id = f.lead_id and s.started_at < f.as_of_timestamp
      and (s.stopped_at is null or s.stopped_at >= f.as_of_timestamp))
union all
select 'capacity snapshot after the decision', c.decision_id || '|' || c.rm_id
from {{ ref('mart_routing_candidate') }} c
where c.capacity_snapshot_as_of_utc > c.as_of_timestamp
union all
select 'prior owner assigned at or after the decision', f.decision_id
from {{ ref('int_lead_routing__priority_features_as_of') }} f
where f.prior_owner_rm_id is not null
  and not exists (select 1 from {{ ref('stg_crm__lead_assignment') }} a2
                   where a2.lead_id = f.lead_id and a2.rm_id = f.prior_owner_rm_id and a2.assigned_at < f.as_of_timestamp)
union all
select 'status at decision taken from a later change', f.decision_id
from {{ ref('int_lead_routing__priority_features_as_of') }} f
where f.lead_status_at_decision <> 'NEW' and not exists (
    select 1 from {{ ref('stg_crm__lead_status_history') }} h
    where h.lead_id = f.lead_id and h.to_status = f.lead_status_at_decision and h.changed_at <= f.as_of_timestamp)
