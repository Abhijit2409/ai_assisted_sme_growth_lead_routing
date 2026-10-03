-- Every RM the operational recommendation selects passed every mandatory eligibility rule, and every queue result
-- maps to a declared queue with an actionable owner.
with op as (select * from {{ ref('mart_routing_recommendation_operational') }}),
     cand as (select * from {{ ref('mart_routing_candidate') }}),
     q as (select * from {{ ref('routing_queue_rules') }})

select 'an operationally recommended RM was not an eligible candidate at that decision' as failure,
       count(*)::varchar as detail
from op
where op.recommended_rm_id is not null
  and not exists (select 1 from cand c
                  where c.decision_id = op.decision_id and c.rm_id = op.recommended_rm_id and c.is_eligible)
having count(*) > 0

union all
select 'an operationally recommended RM failed a mandatory rule', count(*)::varchar
from op join cand c on c.decision_id = op.decision_id and c.rm_id = op.recommended_rm_id
where op.recommended_rm_id is not null
  and not (c.passes_rel_06_employment and c.passes_rel_07_availability and c.passes_rel_08_territory
           and c.passes_rel_09_pool and c.passes_rel_10_specialist and c.passes_rel_11_language
           and c.passes_rel_12_snapshot and coalesce(c.passes_rel_13_hard_limit, false)
           and c.passes_rel_14_exclusion)
having count(*) > 0

union all
select 'a mandatory-specialist lead was operationally assigned to a non-specialist', count(*)::varchar
from op join cand c on c.decision_id = op.decision_id and c.rm_id = op.recommended_rm_id
where op.specialist_requirement_level = 'mandatory' and not c.holds_primary_specialization
having count(*) > 0

union all
select 'an operational queue code is not a declared queue', count(*)::varchar
from op where op.queue_code is not null and op.queue_code not in (select queue_code from q)
having count(*) > 0

union all
select 'an operational queue result has no owner, SLA, next action or escalation path', count(*)::varchar
from op where op.queue_code is not null
  and (op.queue_owner_role is null or op.queue_sla_id is null
       or op.queue_next_action is null or op.queue_escalation_path is null)
having count(*) > 0

union all
select 'a lead held because no eligible RM had capacity is not in the declared capacity queue', count(*)::varchar
from op where op.recommendation_status = 'queued' and op.eligible_candidate_count > 0
  and op.queue_code is distinct from (select value from {{ ref('routing_capacity_ledger_config') }}
                                      where parameter = 'unassigned_queue_code')
having count(*) > 0
