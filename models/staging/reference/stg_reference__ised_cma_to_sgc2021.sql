-- Reviewed crosswalk E2. source_label (the verbatim ISED CMA row text) is the key: ISED publishes no codes.
select * from {{ ref('ised_cma_to_sgc2021') }}
