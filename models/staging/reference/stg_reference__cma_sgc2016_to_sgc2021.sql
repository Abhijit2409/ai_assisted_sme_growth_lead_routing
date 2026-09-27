-- Reviewed crosswalk E1, typed. Blank source codes (6 target-only CMAs) are NULL.
select * replace (cast(preliminary_csd_additions as bigint) as preliminary_csd_additions)
from {{ ref('cma_sgc2016_to_sgc2021') }}
