{{ config(tags=['crm_data']) }}
-- RM-owned activities fall inside an assignment of the same lead and RM (assignment precedes activity); contact only with consent.
select x.activity_id from {{ ref('stg_crm__lead_activity') }} x
join {{ ref('stg_crm__lead_assignment') }} a using (assignment_id)
join {{ ref('stg_crm__lead') }} l on l.lead_id = x.lead_id
where a.lead_id <> x.lead_id or a.rm_id is distinct from x.rm_id or x.activity_at < a.assigned_at
   or (a.ended_at is not null and x.activity_at > a.ended_at) or not l.consent_to_contact_flag
