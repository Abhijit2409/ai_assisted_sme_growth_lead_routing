{{ config(tags=['lead_routing']) }}
-- Required tests: the capacity snapshot is never after the decision and is always the latest valid one, the published
-- capacity fields match the snapshot, and no ordinary recommendation is made at or above the hard limit.
with hard as (select cast(value as double) h from {{ ref('routing_evaluation_thresholds') }} where parameter = 'capacity_hard_limit_ratio')
select 'snapshot after the decision' as issue, c.decision_id || '|' || c.rm_id as id
from {{ ref('mart_routing_candidate') }} c where c.capacity_snapshot_as_of_utc > c.as_of_timestamp
union all
select 'a later valid snapshot existed at or before the decision', c.decision_id || '|' || c.rm_id
from {{ ref('mart_routing_candidate') }} c
where c.capacity_snapshot_id is not null
  and exists (select 1 from {{ ref('stg_crm__rm_capacity_snapshot') }} s
               where s.rm_id = c.rm_id and s.snapshot_as_of_utc <= c.as_of_timestamp
                 and s.snapshot_as_of_utc > c.capacity_snapshot_as_of_utc)
union all
select 'published capacity does not match the snapshot', c.decision_id || '|' || c.rm_id
from {{ ref('mart_routing_candidate') }} c
join {{ ref('stg_crm__rm_capacity_snapshot') }} s on s.capacity_snapshot_id = c.capacity_snapshot_id
where cast(s.utilization_ratio as double) is distinct from c.utilization_ratio_at_decision
   or cast(s.weighted_workload_points as double) is distinct from c.weighted_workload_points
   or s.availability_status is distinct from c.availability_status
union all
select 'recommendation at or above the hard limit', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r cross join hard
where r.recommended_rm_id is not null and r.recommended_utilization_ratio >= hard.h
union all
select 'eligible candidate at or above the hard limit or without a snapshot', c.decision_id || '|' || c.rm_id
from {{ ref('mart_routing_candidate') }} c cross join hard
where c.is_eligible and (c.utilization_ratio_at_decision is null or c.capacity_snapshot_id is null
                         or c.utilization_ratio_at_decision >= hard.h)
