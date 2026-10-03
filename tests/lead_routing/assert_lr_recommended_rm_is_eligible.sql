{{ config(tags=['lead_routing']) }}
-- Required tests: the recommended RM is eligible, holds a valid territory on the decision date, meets a mandatory
-- specialist requirement, is employed and available, and all effective dates cover the decision date.
select 'recommended RM is not eligible' as issue, r.decision_id as id
from {{ ref('mart_routing_recommendation_static') }} r
join {{ ref('mart_routing_candidate') }} c on c.decision_id = r.decision_id and c.rm_id = r.recommended_rm_id
where r.recommended_rm_id is not null and not c.is_eligible
union all
-- Valid territory: the lead's own territory, or, under the declared TER-10 fallback, the national specialist
-- territory for a mandatory-specialist lead that had no eligible regional candidate.
select 'recommended RM has no effective territory', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where r.recommended_rm_id is not null
  and not exists (select 1 from {{ ref('stg_crm__rm_territory') }} t
                   where t.rm_id = r.recommended_rm_id and t.territory_id = r.territory_id
                     and t.valid_from <= r.as_of_date and (t.valid_to is null or t.valid_to > r.as_of_date))
  and not (r.recommended_via_national_pool
           and r.specialist_requirement_level = 'mandatory'
           and exists (select 1 from {{ ref('stg_crm__rm_territory') }} t
                        join {{ ref('stg_crm__territory') }} nt on nt.territory_id = t.territory_id
                                                               and nt.territory_type = 'national_specialist'
                        where t.rm_id = r.recommended_rm_id
                          and t.valid_from <= r.as_of_date and (t.valid_to is null or t.valid_to > r.as_of_date)))
union all
-- The national fallback is a second stage: it may only be used when no regional candidate was eligible.
select 'national fallback used although a regional candidate was eligible', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
join {{ ref('mart_routing_candidate') }} c on c.decision_id = r.decision_id and c.rm_id = r.recommended_rm_id
where r.recommended_via_national_pool and c.decision_has_regional_candidate
union all
select 'mandatory specialist requirement not met', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where r.recommended_rm_id is not null and r.specialist_requirement_level = 'mandatory'
  and not exists (select 1 from {{ ref('stg_crm__rm_specialization') }} s
                   where s.rm_id = r.recommended_rm_id and s.industry_code = r.industry_code
                     and s.specialization_role = 'primary'
                     and s.valid_from <= r.as_of_date and (s.valid_to is null or s.valid_to > r.as_of_date))
union all
select 'recommended RM not employed on the decision date', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
join {{ ref('stg_crm__relationship_manager') }} m on m.rm_id = r.recommended_rm_id
where r.recommended_rm_id is not null
  and (not m.active_flag or m.employment_start_date > r.as_of_date
       or (m.employment_end_date is not null and m.employment_end_date <= r.as_of_date))
union all
select 'recommended RM unavailable in the as-of snapshot', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where r.recommended_rm_id is not null
  and (r.recommended_rm_availability_status is null
       or r.recommended_rm_availability_status in ('planned_leave', 'unavailable'))
union all
select 'explicitly stated language not supported', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
join {{ ref('stg_crm__relationship_manager') }} m on m.rm_id = r.recommended_rm_id
where r.recommended_rm_id is not null and r.stated_service_language in ('en', 'fr')
  and ('|' || m.supported_service_languages || '|') not like ('%|' || r.stated_service_language || '|%')
union all
select 'reassignment returned the lead to its prior owner', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
join {{ ref('int_lead_routing__priority_features_as_of') }} f on f.decision_id = r.decision_id
where r.decision_type = 'reassignment' and r.recommended_rm_id is not null
  and f.prior_owner_rm_id is not null and r.recommended_rm_id = f.prior_owner_rm_id
