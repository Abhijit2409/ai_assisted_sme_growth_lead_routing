{{ config(tags=['opportunity']) }}
-- Every normalized value lies in 0-100 (percentile: exactly 0 at the weakest and 100 at the strongest market), and a
-- higher oriented value never receives a lower normalized value: higher normalized = stronger opportunity evidence.
-- For lower_is_stronger components the oriented value is the negated transformed value.
with n as (select * from {{ ref('int_opportunity__normalized_indicators') }})
select 'normalized value out of range' as issue, component_id, cma_code_sgc2021 from n
where normalized_percentile not between 0 and 100 or normalized_min_max not between 0 and 100
   or normalized_min_max_winsorized not between 0 and 100 or normalized_robust_z not between 0 and 100
union all
select 'percentile does not span 0-100', component_id, null from n group by component_id
having min(normalized_percentile) <> 0 or max(normalized_percentile) <> 100
union all
select 'orientation wrong', component_id, cma_code_sgc2021 from n
where directed_value <> case when direction = 'lower_is_stronger' then -transformed_value else transformed_value end
union all
select 'normalization not monotone in the oriented value', a.component_id, a.cma_code_sgc2021
from n as a join n as b on a.component_id = b.component_id and a.directed_value > b.directed_value
where a.normalized_percentile <= b.normalized_percentile or a.normalized_min_max < b.normalized_min_max
   or a.normalized_robust_z < b.normalized_robust_z or a.normalized_min_max_winsorized < b.normalized_min_max_winsorized
union all
select 'tied values normalized differently', a.component_id, a.cma_code_sgc2021
from n as a join n as b on a.component_id = b.component_id and a.directed_value = b.directed_value and a.cma_code_sgc2021 < b.cma_code_sgc2021
where a.normalized_percentile <> b.normalized_percentile
