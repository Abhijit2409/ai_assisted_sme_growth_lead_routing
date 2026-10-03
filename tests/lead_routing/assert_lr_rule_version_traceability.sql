{{ config(tags=['lead_routing']) }}
-- Required test: rule-version traceability. Every published row carries the declared rule version, every referenced
-- rule exists in the declared configuration seeds, and the seeds agree with the project variable.
select 'missing or wrong rule version on mart_lead_priority' as issue, decision_id as id from {{ ref('mart_lead_priority') }}
where rule_version is distinct from '{{ var("lead_routing_rule_version") }}'
union all
select 'missing or wrong rule version on mart_routing_recommendation_static', decision_id from {{ ref('mart_routing_recommendation_static') }}
where rule_version is distinct from '{{ var("lead_routing_rule_version") }}'
union all
select 'missing or wrong rule version on mart_routing_exception', decision_id from {{ ref('mart_routing_exception') }}
where rule_version is distinct from '{{ var("lead_routing_rule_version") }}'
union all
select 'missing or wrong rule version on mart_routing_candidate', decision_id || '|' || rm_id from {{ ref('mart_routing_candidate') }}
where rule_version is distinct from '{{ var("lead_routing_rule_version") }}'
union all
select 'unknown eligibility rule', p.decision_id from {{ ref('mart_lead_priority') }} p
where not exists (select 1 from {{ ref('lead_priority_eligibility_rules') }} e where e.rule_id = p.eligibility_rule_id)
union all
select 'seed rule version does not match the project variable', rule_version
from {{ ref('lead_priority_weight_config') }} where rule_version <> '{{ var("lead_routing_rule_version") }}'
union all
select 'seed rule version does not match the project variable', rule_version
from {{ ref('routing_preference_weight_config') }} where rule_version <> '{{ var("lead_routing_rule_version") }}'
