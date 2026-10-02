{{ config(tags=['crm_data']) }}
-- Every lead keeps its original touch; merged leads' touches move to the survivor; one first touch per surviving lead.
select l.lead_id from {{ ref('stg_crm__lead') }} l
where (select count(*) from {{ ref('stg_crm__lead_source_touch') }} t where t.original_lead_id = l.lead_id and t.channel_code = l.first_channel_code
       and t.touched_at = l.captured_at) <> 1
   or (l.current_lead_status <> 'MERGED' and (select count(*) from {{ ref('stg_crm__lead_source_touch') }} t where t.lead_id = l.lead_id and t.is_first_touch) <> 1)
   or (l.current_lead_status = 'MERGED' and exists (select 1 from {{ ref('stg_crm__lead_source_touch') }} t where t.lead_id = l.lead_id))
union all
select t.original_lead_id from {{ ref('stg_crm__lead_source_touch') }} t join {{ ref('stg_crm__lead') }} o on o.lead_id = t.original_lead_id
where t.lead_id <> t.original_lead_id and o.surviving_lead_id is distinct from t.lead_id
