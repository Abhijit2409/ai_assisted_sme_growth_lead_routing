{{ config(tags=['crm_data']) }}
-- Manual overrides record a reason code and actor role; capacity exceptions occur only with an override.
select assignment_id from {{ ref('stg_crm__lead_assignment') }}
where (manual_override_flag and (override_reason_code is null or override_user_role is null))
   or (not manual_override_flag and (override_reason_code is not null or override_user_role is not null or capacity_exception_flag))
