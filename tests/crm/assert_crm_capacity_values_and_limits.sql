{{ config(tags=['crm_data']) }}
-- Capacity values are consistent with the CAP rules: utilization = workload / capacity, states follow the thresholds, exceptions stay within the ceiling.
with c as (select max(case when rule_id = 'CAP-06' then cast(warning_level as double) end) w, max(case when rule_id = 'CAP-06' then cast(hard_limit as double) end) h,
                  max(case when rule_id = 'CAP-09' then cast(hard_limit as double) end) x, max(case when rule_id = 'CAP-05' then cast(threshold as double) end) base
           from {{ source('crm_design', 'rm_capacity_rule_catalog') }})
select s.capacity_snapshot_id from {{ ref('stg_crm__rm_capacity_snapshot') }} s, c
where s.available_capacity_points <> c.base * case s.availability_status when 'available' then 1 when 'partial' then 0.5 else 0 end
   or s.weighted_workload_points < 0 or s.specialist_support_points > s.weighted_workload_points
   or (s.utilization_ratio is null) <> (s.available_capacity_points = 0)
   or abs(s.utilization_ratio - s.weighted_workload_points / nullif(s.available_capacity_points, 0)) > 0.00005 + 1e-9
   or s.capacity_state <> case when s.utilization_ratio is null then 'unavailable'
        when s.utilization_ratio >= c.h and s.capacity_state = 'approved_exception' and s.utilization_ratio <= c.x then 'approved_exception'
        when s.utilization_ratio >= c.h then 'at_or_above_hard_limit' when s.utilization_ratio >= c.w then 'warning' else 'below_warning' end
