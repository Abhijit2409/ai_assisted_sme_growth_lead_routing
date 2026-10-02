{{ config(tags=['crm_data']) }}
-- Ordinary decisions never pick an RM at or above the hard limit or unavailable in the snapshot available at decision time.
with hard as (select cast(hard_limit as double) h from {{ source('crm_design', 'rm_capacity_rule_catalog') }} where rule_id = 'CAP-06')
select d.routing_decision_id from {{ ref('stg_crm__routing_decision') }} d
asof join {{ ref('stg_crm__rm_capacity_snapshot') }} s on s.rm_id = d.selected_rm_id and s.snapshot_as_of_utc <= d.decided_at
where d.capacity_utilization_at_decision is distinct from s.utilization_ratio
   or (d.routing_outcome <> 'override_assigned' and (s.utilization_ratio >= (select h from hard) or s.availability_status in ('planned_leave', 'unavailable')))
