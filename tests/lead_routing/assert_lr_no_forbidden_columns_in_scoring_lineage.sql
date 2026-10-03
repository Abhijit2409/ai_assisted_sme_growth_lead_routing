{{ config(tags=['lead_routing']) }}
-- Required tests: no opportunity outcome, no ground truth and no historical selected RM in the scoring or routing
-- lineage. Every model that produces the priority score, the eligibility, the preference, the ranking or the queue
-- result is checked for outcome, ground-truth and historical-decision column names. The comparison columns on
-- mart_routing_recommendation_static are deliberately excluded: they are produced after the recommendation exists.
select table_schema, table_name, column_name
from information_schema.columns
where table_name in (
        'int_lead_routing__decision_population', 'int_lead_routing__priority_features_as_of',
        'int_lead_routing__priority_eligibility', 'int_lead_routing__priority_components',
        'int_lead_routing__lead_priority_score', 'int_lead_routing__routing_decision_population',
        'int_lead_routing__candidate_eligibility', 'int_lead_routing__candidate_preference',
        'int_lead_routing__candidate_ranking', 'int_lead_routing__queue_outcome',
        'mart_lead_priority', 'mart_routing_candidate', 'mart_routing_exception')
  and regexp_matches(column_name, 'historical|selected_rm|opportunity_id|opportunity_stage|opportunity_status'
                                || '|conversion|converted|closed_lost|qualified_at|won_|revenue|propensity'
                                || '|expected_|planned_path|latent_|arrival_kind|injected_|truth|routing_outcome'
                                || '|primary_scenario|synthetic_scenario')
  and column_name not in ('market_opportunity_score', 'market_opportunity_classification',
                          'market_opportunity_rank', 'market_scenario_id')
