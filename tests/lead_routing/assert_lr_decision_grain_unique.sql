{{ config(tags=['lead_routing']) }}
-- Required test: unique decision grain. One row per lead per routing-decision timestamp in every model on that grain.
with g as (
    select 'mart_lead_priority' m, lead_id, as_of_timestamp, count(*) n from {{ ref('mart_lead_priority') }} group by 1,2,3
    union all
    select 'mart_routing_recommendation_static', lead_id, as_of_timestamp, count(*) from {{ ref('mart_routing_recommendation_static') }} group by 1,2,3
    union all
    select 'int_lead_routing__decision_population', lead_id, as_of_timestamp, count(*) from {{ ref('int_lead_routing__decision_population') }} group by 1,2,3
)
select * from g where n > 1
