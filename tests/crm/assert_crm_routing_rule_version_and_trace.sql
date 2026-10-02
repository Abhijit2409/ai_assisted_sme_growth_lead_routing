{{ config(tags=['crm_data']) }}
-- Decisions and assignments reference the routing rule version effective at the time; traces use catalogued rules; initial and queue-release decisions see READY_FOR_ASSIGNMENT.
select d.routing_decision_id from {{ ref('stg_crm__routing_decision') }} d
left join {{ ref('stg_crm__rule_version') }} v on v.rule_version_id = d.rule_version_id
where v.rule_set <> 'routing' or d.decided_at < v.effective_from or (v.effective_to is not null and d.decided_at >= v.effective_to)
   or (d.decision_type in ('initial', 'queue_release') and d.lead_status_at_decision <> 'READY_FOR_ASSIGNMENT')
   or exists (select 1 from (select unnest(string_split(d.rule_trace, ';')) r) x
              where x.r not in (select rule_id from {{ source('crm_design', 'routing_rule_catalog') }}))
union all
select a.assignment_id from {{ ref('stg_crm__lead_assignment') }} a
join {{ ref('stg_crm__rule_version') }} v using (rule_version_id) where v.rule_set <> 'routing'
