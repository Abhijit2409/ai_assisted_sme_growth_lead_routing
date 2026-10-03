{{ config(tags=['lead_routing']) }}
-- The framework never overwrites the source priority field: every published source_priority_band equals the value on
-- the synthetic lead, and no CRM source, staging or foundational mart model carries a recommendation column.
select 'source priority band changed' as issue, p.decision_id as id
from {{ ref('mart_lead_priority') }} p
join {{ ref('stg_crm__lead') }} l on l.lead_id = p.lead_id
where p.source_priority_band is distinct from l.priority_band
union all
select 'recommendation column leaked into a CRM model', table_schema || '.' || table_name || '.' || column_name
from information_schema.columns
where (table_name like 'stg_crm__%'
       or table_name in ('fct_lead', 'fct_lead_assignment', 'fct_routing_decision', 'fct_rm_capacity_snapshot'))
  and regexp_matches(column_name, 'recommended_|preference_score|priority_score')
