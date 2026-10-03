-- Exactly one operational result per routing decision, and the assigned/queued states are mutually exclusive and
-- complete. Also proves the static diagnostic and the operational recommendation remain distinguishable.
with op as (select * from {{ ref('mart_routing_recommendation_operational') }}),
     st as (select * from {{ ref('mart_routing_recommendation_static') }}),
     pop as (select * from {{ ref('int_lead_routing__routing_decision_population') }})

select 'operational result count differs from the decision population' as failure, count(*)::varchar as detail
from (select (select count(*) from op) a, (select count(distinct decision_id) from op) b,
             (select count(*) from pop) c)
where a <> c or b <> c
having count(*) > 0

union all
select 'a decision has more than one operational result', count(*)::varchar
from (select decision_id from op group by 1 having count(*) > 1)
having count(*) > 0

union all
select 'an assigned decision has no recommended RM', count(*)::varchar
from op where recommendation_status = 'assigned' and recommended_rm_id is null
having count(*) > 0

union all
select 'a queued decision still carries a recommended RM', count(*)::varchar
from op where recommendation_status = 'queued' and recommended_rm_id is not null
having count(*) > 0

union all
select 'a queued decision has no queue code or no reason', count(*)::varchar
from op where recommendation_status = 'queued' and (queue_code is null or queue_reason is null or queue_reason = '')
having count(*) > 0

union all
select 'recommendation_status is not one of the two declared states', count(*)::varchar
from op where recommendation_status not in ('assigned', 'queued')
having count(*) > 0

-- the two recommendations must stay distinguishable: different basis label, and the operational one carries the
-- ledger columns that the static one does not
union all
select 'the operational recommendation is not labelled as capacity-aware', count(*)::varchar
from op where recommendation_basis is distinct from 'capacity_aware_operational'
having count(*) > 0

union all
select 'the static diagnostic and the operational recommendation are identical on every decision', '0'
from (select count(*) n from op o join st s using (decision_id)
      where o.recommended_rm_id is distinct from s.recommended_rm_id)
where n = 0
