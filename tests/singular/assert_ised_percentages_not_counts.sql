-- ISED percentages stay percentages: percent estimates carry a percent unit and lie in 0-100, and the only
-- count-type rows are the published weighted SME counts and respondent counts of Table 49.
select 'percentage estimate without a percent unit or outside 0-100' as issue, measure_id, segment_key
from {{ ref('fact_ised_financing_context') }}
where estimate_type in ('percentage_of_universe', 'percentage_share_of_total', 'ratio_of_totals')
  and (statistical_unit not like '%\%%' escape '\' or value_published < 0 or value_published > 100)
union all
select 'count-type row outside Table 49', measure_id, segment_key from {{ ref('fact_ised_financing_context') }}
where estimate_type in ('weighted_count', 'unweighted_respondent_count') and ised_table <> '49'
union all
select 'count-type unit on a percentage row', measure_id, segment_key from {{ ref('fact_ised_financing_context') }}
where estimate_type like 'percentage%' and statistical_unit like '%enterprises%'
