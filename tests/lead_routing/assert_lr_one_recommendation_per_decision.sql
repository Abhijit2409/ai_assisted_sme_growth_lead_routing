{{ config(tags=['lead_routing']) }}
-- Required test: exactly one recommendation per decision, and it is either one RM or one queue, never both or neither.
select 'decision missing from the recommendation mart' as issue, d.decision_id as id
from {{ ref('int_lead_routing__decision_population') }} d
where not exists (select 1 from {{ ref('mart_routing_recommendation_static') }} r where r.decision_id = d.decision_id)
union all
select 'both an RM and a queue, or neither', r.decision_id
from {{ ref('mart_routing_recommendation_static') }} r
where (r.recommended_rm_id is not null) = (r.recommended_queue_code is not null)
union all
select 'more than one rank-1 candidate', cast(decision_id as varchar)
from (select decision_id, count(*) n from {{ ref('int_lead_routing__candidate_ranking') }}
      where is_recommended_candidate group by 1) where n > 1
