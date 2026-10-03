{{ config(tags=['lead_routing']) }}
-- Required tests: a valid queue whenever no RM is eligible, and every queue result carries a code, reason, failed
-- rules, owner, SLA, next action and escalation path.
select 'no eligible candidate but no queue result' as issue, p.decision_id as id
from {{ ref('int_lead_routing__routing_decision_population') }} p
where not exists (select 1 from {{ ref('int_lead_routing__candidate_ranking') }} k where k.decision_id = p.decision_id)
  and not exists (select 1 from {{ ref('int_lead_routing__queue_outcome') }} q where q.decision_id = p.decision_id)
union all
select 'eligible candidates exist but the decision was queued', q.decision_id
from {{ ref('int_lead_routing__queue_outcome') }} q
where exists (select 1 from {{ ref('int_lead_routing__candidate_ranking') }} k where k.decision_id = q.decision_id)
union all
select 'incomplete queue result', e.decision_id
from {{ ref('mart_routing_exception') }} e
where e.queue_code is null or e.queue_reason is null or e.failed_rule_codes is null or e.failed_rule_codes = ''
   or e.queue_owner_role is null or e.queue_sla_id is null or e.queue_sla_target is null
   or e.next_action is null or e.escalation_path is null
union all
select 'queued recommendation without a full reason', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where r.recommendation_outcome = 'queue'
  and (r.recommendation_reason is null or r.queue_owner_role is null or r.queue_next_action is null
       or r.queue_escalation_path is null or r.queue_reason is null)
union all
select 'rm recommendation without a reason', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where r.recommendation_outcome = 'rm_assignment'
  and (r.recommendation_reason is null or r.recommendation_reason = ''
       or r.recommended_capacity_snapshot_id is null or r.recommended_preference_score is null)
