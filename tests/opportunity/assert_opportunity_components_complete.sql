{{ config(tags=['opportunity']) }}
-- No full-score market has a missing required component, pillar score or weight.
select cma_code_sgc2021, scenario_id from {{ ref('mart_cma_opportunity_framework') }}
where c01_normalized is null or c03_normalized is null or c05_normalized is null or c09_normalized is null or c15_normalized is null
   or c10_stability_normalized is null or pillar_scale is null or pillar_momentum is null or pillar_dynamism is null
   or pillar_commercial_fit is null or pillar_stability is null or opportunity_score is null or score_rank is null
   or weight_scale is null or weight_momentum is null or weight_dynamism is null or weight_commercial_fit is null or weight_stability is null
