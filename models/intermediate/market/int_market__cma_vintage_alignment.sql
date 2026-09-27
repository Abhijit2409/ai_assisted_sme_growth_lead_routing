-- Grain: one row per SGC 2021 CMA (41), from reviewed crosswalk E1.
-- 35 CMAs share a code with SGC 2016 (boundaries not verified at component level); 6 are target-only.
select
    source_code                     as cma_code_sgc2016,
    target_code                     as cma_code_sgc2021,
    source_dguid                    as dguid_2016,
    target_dguid                    as dguid_2021,
    source_label                    as cma_label_sgc2016,
    target_label                    as cma_label_sgc2021,
    mapping_type,
    mapping_status,
    boundary_change_flag,
    preliminary_csd_additions,
    source_code is not null         as has_2016_source,
    false                           as exact_boundary_claimed,
    crosswalk_id,
    evidence_url
from {{ ref('stg_reference__cma_sgc2016_to_sgc2021') }}
