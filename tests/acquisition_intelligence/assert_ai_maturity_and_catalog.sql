-- The maturity rule must actually be applied, the planning snapshot must not leak into historical months, and the
-- KPI catalog must describe the KPIs the marts really publish.
with funnel as (select * from {{ ref('mart_acquisition_funnel_monthly') }}),
     spine  as (select * from {{ ref('int_acq__lead_spine') }}),
     cat    as (select * from {{ ref('acquisition_kpi_catalog') }}),
     prm    as (select * from {{ ref('acquisition_reporting_parameters') }})

-- the funnel's mature denominator must equal the spine's mature population exactly: no immature lead may leak in
select 'the funnel mature denominator does not match the spine' as failure,
       ((select coalesce(sum(valid_leads_mature), 0) from funnel)::varchar || ' vs '
        || (select count(*) from spine where is_conversion_mature and is_valid_lead)::varchar) as detail
from (select 1)
where (select coalesce(sum(valid_leads_mature), 0) from funnel)
   <> (select count(*) from spine where is_conversion_mature and is_valid_lead)

union all
select 'a lead is both mature and immature for conversion', count(*)::varchar
from spine
where is_conversion_mature and days_since_capture <
      (select cast(value as integer) from prm where parameter = 'conversion_maturity_days')
having count(*) > 0

union all
select 'a recent cohort still reports a mature conversion denominator', count(*)::varchar
from funnel f
cross join (select cast(value as date) as cutoff from prm where parameter = 'reporting_cutoff_date') c
cross join (select cast(value as integer) as d from prm where parameter = 'conversion_maturity_days') m
where f.reporting_month > (c.cutoff - cast(m.d as integer) * interval 1 day)
  and f.valid_leads_mature > 0
having count(*) > 0

-- the immature population must be published, not silently dropped
union all
select 'immature leads are not reconciled: mature plus immature does not equal valid', count(*)::varchar
from funnel
where valid_leads_mature + valid_leads_immature <> valid_leads
having count(*) > 0

-- the current planning snapshot must not be applied retrospectively: the monthly funnel carries no opportunity score
union all
select 'the monthly funnel carries a current-snapshot opportunity score', count(*)::varchar
from information_schema.columns
where table_name = 'mart_acquisition_funnel_monthly'
  and regexp_matches(column_name, 'opportunity_score|opportunity_rank|opportunity_classification|pillar_')
having count(*) > 0

-- the KPI catalog must be complete and must name real marts
union all
select 'a KPI catalog entry has an incomplete definition', count(*)::varchar
from cat
where business_definition is null or business_definition = ''
   or formula is null or formula = ''
   or numerator is null or numerator = ''
   or denominator is null or denominator = ''
   or grain is null or grain = ''
   or filters is null or filters = ''
   or maturity_rule is null or maturity_rule = ''
   or null_handling is null or null_handling = ''
   or interpretation is null or interpretation = ''
   or limitation is null or limitation = ''
   or stakeholder is null or stakeholder = ''
having count(*) > 0

union all
select 'a KPI catalog entry names a mart that does not exist', count(*)::varchar
from cat
where source_mart not in (select table_name from information_schema.tables where table_catalog = 'sme_banking')
having count(*) > 0
