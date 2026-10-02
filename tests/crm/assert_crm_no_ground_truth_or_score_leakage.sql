{{ config(tags=['crm_data']) }}
-- No CRM model exposes ground-truth labels, scores or RM recommendations, and fictional contact fields never reach the analytics schema.
select table_schema, table_name, column_name from information_schema.columns
where (table_name like '%crm%' or table_name in ('dim_relationship_manager', 'dim_acquisition_channel', 'dim_campaign', 'dim_business_prospect',
       'fct_lead', 'fct_lead_status_history', 'fct_lead_assignment', 'fct_lead_activity', 'fct_routing_decision', 'fct_rm_capacity_snapshot',
       'fct_sla_event', 'fct_opportunity_outcome', 'fct_lead_source_touch'))
  and (column_name in ('primary_scenario_id', 'scenario_id', 'synthetic_scenario_id', 'arrival_kind', 'latent_engagement', 'planned_path',
                       'injected_process_issues', 'expected_final_status', 'expected_conversion', 'expected_duplicate_outcome')
       or regexp_matches(column_name, 'priority_score|routing_score|lead_score|recommended_rm|rm_recommendation|propensity')
       or (table_schema = 'analytics' and regexp_matches(column_name, '^contact_(email|phone)')))
