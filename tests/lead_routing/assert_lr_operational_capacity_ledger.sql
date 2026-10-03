-- The capacity ledger: no operational recommendation exceeds the declared limit, capacity is genuinely consumed
-- across sequential recommendations, and a missing capacity snapshot is never reported as a hard-limit breach.
with op as (select * from {{ ref('mart_routing_recommendation_operational') }}),
     cfg as (select max(case when parameter = 'assignment_workload_points' then cast(value as double) end) as pts,
                    max(case when parameter = 'post_assignment_limit_ratio' then cast(value as double) end) as ratio
             from {{ ref('routing_capacity_ledger_config') }}),
     cand as (select * from {{ ref('mart_routing_candidate') }})

select 'an operational recommendation exceeds the declared post-assignment capacity limit' as failure,
       count(*)::varchar as detail
from op cross join cfg
where op.recommended_rm_id is not null
  and op.simulated_post_assignment_workload_points >= op.capacity_limit_points * cfg.ratio - 1e-9
having count(*) > 0

union all
select 'the published post-assignment workload does not equal observed + simulated + the declared assignment weight',
       count(*)::varchar
from op cross join cfg
where op.recommended_rm_id is not null
  and abs(op.simulated_post_assignment_workload_points
          - (op.observed_workload_points_at_snapshot + op.simulated_workload_added_since_snapshot + cfg.pts)) > 1e-9
having count(*) > 0

union all
select 'the recommendation did not add the declared assignment workload', count(*)::varchar
from op cross join cfg
where op.recommended_rm_id is not null and op.workload_points_added_by_this_recommendation is distinct from cfg.pts
having count(*) > 0

-- capacity must actually accumulate: within one RM and one capacity snapshot, the simulated workload already added
-- must rise by exactly the declared weight with each successive recommendation
union all
select 'the ledger does not accumulate by the declared weight within an RM and snapshot', count(*)::varchar
from (
    select o.decision_id,
           o.simulated_workload_added_since_snapshot as added,
           (row_number() over (partition by o.recommended_rm_id, o.capacity_snapshot_id
                               order by o.processing_sequence) - 1) * c.pts as expected
    from op o cross join cfg c
    where o.recommended_rm_id is not null
) where abs(added - expected) > 1e-9
having count(*) > 0

union all
select 'capacity is never consumed: no recommendation was ever made against an already-loaded ledger', '0'
from (select count(*) n from op where simulated_workload_added_since_snapshot > 0) where n = 0

-- a missing or unusable capacity snapshot is a capacity-data problem (REL-12), never a hard-limit breach (REL-13)
union all
select 'a candidate with no usable capacity snapshot is reported as a hard-capacity failure', count(*)::varchar
from cand where not passes_rel_12_snapshot and passes_rel_13_hard_limit is false
having count(*) > 0

union all
select 'REL-13 is reported in the failed rules although it could not be evaluated', count(*)::varchar
from cand where not rel_13_hard_limit_evaluated and failed_rule_codes like '%REL-13%'
having count(*) > 0

union all
select 'REL-13 was evaluated although no capacity could be established', count(*)::varchar
from cand where rel_13_hard_limit_evaluated and utilization_ratio_at_decision is null
having count(*) > 0
