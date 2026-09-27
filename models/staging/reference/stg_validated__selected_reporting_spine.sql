-- The approved 41-CMA reporting spine; used only to test the tiers that dbt derives.
select
    cma_code_sgc2021, cma_label_sgc2021, cma_code_sgc2016, vintage_status, cast(tier as bigint) as tier,
    ised_cma_label, has_momentum_0722 = 'True' as has_momentum_0722, has_ised_cma_context = 'True' as has_ised_cma_context,
    in_primary_decision_set = 'True' as in_primary_decision_set, evidence_label
from {{ source('validated_profiles', 'selected_reporting_spine') }}
