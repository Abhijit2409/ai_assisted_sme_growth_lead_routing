-- The action queue holds exactly one row per actionable lead, excludes terminal leads that need nothing, and is
-- deterministic.
with queue as (select * from {{ ref('mart_acquisition_action_queue') }}),
     spine as (select * from {{ ref('int_acq__lead_spine') }}),
     unresolved as (select distinct duplicate_group_id from {{ ref('int_crm__duplicate_resolution') }}
                    where resolved_at is null)

select 'a lead appears more than once in the action queue' as failure, count(*)::varchar as detail
from (select lead_id from queue group by 1 having count(*) > 1)
having count(*) > 0

union all
select 'an active lead is missing from the action queue', count(*)::varchar
from spine s
where s.is_active and not exists (select 1 from queue q where q.lead_id = s.lead_id)
having count(*) > 0

-- a terminal lead may only appear when its duplicate group is genuinely still unresolved
union all
select 'a terminal lead is in the action queue without a remediation reason', count(*)::varchar
from queue q
where q.is_terminal
  and (q.duplicate_group_id is null
       or q.duplicate_group_id not in (select duplicate_group_id from unresolved))
having count(*) > 0

union all
select 'a converted or closed lead is in the action queue', count(*)::varchar
from queue q
where q.current_lead_status in ('CONVERTED', 'CLOSED_LOST', 'WITHDRAWN', 'INVALID')
having count(*) > 0

-- every row carries an action, an owner and a rank
union all
select 'an action row has no action, owner or rank', count(*)::varchar
from queue
where recommended_next_action is null or recommended_next_action = ''
   or responsible_role is null or responsible_role = ''
   or action_rank is null
having count(*) > 0

-- the rank is a complete, deterministic 1..n permutation
union all
select 'action_rank is not a complete 1..n ordering', count(*)::varchar
from (select count(*) n, count(distinct action_rank) d, min(action_rank) lo, max(action_rank) hi from queue)
where lo <> 1 or hi <> n or d <> n
having count(*) > 0

-- The declared ordering must actually hold between neighbouring ranks. The precedence is expressed once as a
-- comparable sort key, so the assertion is simply that the key never decreases as the rank increases.
union all
select 'the action ordering does not follow the declared precedence', count(*)::varchar
from (
    select action_rank,
           case when requires_escalation then 0 else 1 end                      as k1,
           case sla_status when 'breached and open' then 0 when 'breached' then 1
                           when 'due within 24 hours' then 2 when 'running' then 3 else 4 end as k2,
           case priority_tier when 'P1' then 1 when 'P2' then 2 when 'P3' then 3 else 9 end   as k3,
           -coalesce(priority_score, -1)                                        as k4,
           -lead_age_days                                                       as k5,
           lead_id                                                              as k6
    from queue
) a
join (
    select action_rank,
           case when requires_escalation then 0 else 1 end                      as k1,
           case sla_status when 'breached and open' then 0 when 'breached' then 1
                           when 'due within 24 hours' then 2 when 'running' then 3 else 4 end as k2,
           case priority_tier when 'P1' then 1 when 'P2' then 2 when 'P3' then 3 else 9 end   as k3,
           -coalesce(priority_score, -1)                                        as k4,
           -lead_age_days                                                       as k5,
           lead_id                                                              as k6
    from queue
) b on b.action_rank = a.action_rank + 1
where (a.k1, a.k2, a.k3, a.k4, a.k5, a.k6) > (b.k1, b.k2, b.k3, b.k4, b.k5, b.k6)
having count(*) > 0

-- a queued lead must carry the queue reason it was held for
union all
select 'a queued lead has no queue reason', count(*)::varchar
from queue where queue_code is not null and (queue_reason is null or queue_reason = '')
having count(*) > 0

-- escalation must be justified by a missed promise, an open breach, or an ageing held lead
union all
select 'a lead is escalated without a stated reason', count(*)::varchar
from queue q
where q.requires_escalation
  and not (q.first_contact_sla_breached or q.open_breached_clocks > 0
           or (q.queue_code is not null and q.lead_age_days > 30))
having count(*) > 0
