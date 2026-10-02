{{ config(tags=['crm_data']) }}
-- Identifiers, names, emails and telephone numbers are fixed-width fictional placeholders (reserved .example domains, 555-01xx numbers).
select lead_id from {{ ref('stg_crm__lead') }}
where not regexp_full_match(lead_id, 'LED-\d{7}') or not regexp_full_match(contact_email_fictional, '[a-z0-9.\-]+@[a-z0-9\-]+\.example')
   or not regexp_full_match(contact_phone_fictional, '\+1-\d{3}-555-01\d{2}')
union all
select prospect_id from {{ ref('stg_crm__business_prospect') }}
where not regexp_full_match(prospect_id, 'PRS-\d{6}') or not regexp_full_match(legal_name, '(?i)synthetic (business|operating company) \d{6}[ a-z.]*')
union all
select rm_id from {{ ref('stg_crm__relationship_manager') }} where not regexp_full_match(rm_id, 'RM-\d{4}') or not regexp_full_match(rm_code, 'RM-FICT-\d{4}')
union all
select campaign_id from {{ ref('stg_crm__campaign') }} where not regexp_full_match(campaign_name, 'Campaign Synthetic \d{3} \(fictional\)')
