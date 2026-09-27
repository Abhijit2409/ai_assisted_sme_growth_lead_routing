-- The tiers, flags and labels dbt derives from the crosswalks and source coverage must equal the approved spine.
(select cma_code_sgc2021, cma_label_sgc2021, cma_code_sgc2016, vintage_status, tier, ised_cma_label, has_momentum_0722,
        has_ised_cma_context, in_primary_decision_set, evidence_label from {{ ref('dim_cma') }}
 except
 select cma_code_sgc2021, cma_label_sgc2021, cma_code_sgc2016, vintage_status, tier, ised_cma_label, has_momentum_0722,
        has_ised_cma_context, in_primary_decision_set, evidence_label from {{ ref('stg_validated__selected_reporting_spine') }})
union all
(select cma_code_sgc2021, cma_label_sgc2021, cma_code_sgc2016, vintage_status, tier, ised_cma_label, has_momentum_0722,
        has_ised_cma_context, in_primary_decision_set, evidence_label from {{ ref('stg_validated__selected_reporting_spine') }}
 except
 select cma_code_sgc2021, cma_label_sgc2021, cma_code_sgc2016, vintage_status, tier, ised_cma_label, has_momentum_0722,
        has_ised_cma_context, in_primary_decision_set, evidence_label from {{ ref('dim_cma') }})
