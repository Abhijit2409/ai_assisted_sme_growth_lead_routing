-- One row per CMA pair (35 shared codes + 6 target-only). Boundaries are never treated as exact.
select
    cma_code_sgc2016, cma_code_sgc2021, dguid_2016, dguid_2021, cma_label_sgc2016, cma_label_sgc2021, mapping_type,
    mapping_status, boundary_change_flag, preliminary_csd_additions, has_2016_source, exact_boundary_claimed,
    crosswalk_id, evidence_url
from {{ ref('int_market__cma_vintage_alignment') }}
order by cma_code_sgc2016, cma_code_sgc2021
