{{ config(tags=['crm_data']) }}
-- Opportunities open at qualification; conversions follow a won opportunity; closure never precedes the final activity.
select o.lead_id from {{ ref('stg_crm__opportunity_outcome') }} o
where o.opened_at <> (select min(changed_at) from {{ ref('stg_crm__lead_status_history') }} h where h.lead_id = o.lead_id and h.to_status = 'QUALIFIED')
   or o.closed_at < o.opened_at
union all
select h.lead_id from {{ ref('stg_crm__lead_status_history') }} h
where h.to_status = 'CONVERTED' and not exists (select 1 from {{ ref('stg_crm__opportunity_outcome') }} o
      where o.lead_id = h.lead_id and o.opportunity_status = 'won' and o.opened_at <= h.changed_at)
union all
select l.lead_id from {{ ref('int_crm__lead_current_status') }} l
where l.is_terminal and l.status_changed_at < (select max(activity_at) from {{ ref('stg_crm__lead_activity') }} a where a.lead_id = l.lead_id)
