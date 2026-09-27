-- Joins to reviewed mappings and bridges must never duplicate or drop fact rows.
select 'momentum published rows differ from scoped 0722 rows' as issue,
       (select count(*) from {{ ref('fact_cma_momentum_monthly') }} where value_origin = 'published') as actual,
       (select count(*) from {{ ref('int_market__momentum_published') }}) as expected
where actual <> expected
union all
select 'momentum derived rows differ from derived inputs',
       (select count(*) from {{ ref('fact_cma_momentum_monthly') }} where value_origin = 'derived'),
       (select count(*) from {{ ref('int_market__momentum_derived') }})
where 1 = 1 and (select count(*) from {{ ref('fact_cma_momentum_monthly') }} where value_origin = 'derived')
              <> (select count(*) from {{ ref('int_market__momentum_derived') }})
union all
select 'bridge_cma_vintage join duplicates momentum rows',
       (select count(*) from {{ ref('fact_cma_momentum_monthly') }} f join {{ ref('bridge_cma_vintage') }} b using (cma_code_sgc2016)),
       (select count(*) from {{ ref('fact_cma_momentum_monthly') }})
where (select count(*) from {{ ref('fact_cma_momentum_monthly') }} f join {{ ref('bridge_cma_vintage') }} b using (cma_code_sgc2016))
   <> (select count(*) from {{ ref('fact_cma_momentum_monthly') }})
union all
select 'ISED segment lookup is not one key per row context and text',
       (select count(*) from {{ ref('int_financing__segment_lookup') }}),
       (select count(*) from (select distinct dimension_class, stub_label from {{ ref('int_financing__segment_lookup') }}))
where (select count(*) from {{ ref('int_financing__segment_lookup') }})
   <> (select count(*) from (select distinct dimension_class, stub_label from {{ ref('int_financing__segment_lookup') }}))
union all
select 'ISED fact rows differ from in-scope cells',
       (select count(*) from {{ ref('fact_ised_financing_context') }}),
       (select count(*) from {{ ref('stg_ised__financing_context') }} c join {{ ref('ised_segment_scope') }} s
          on s.table_no = c.table_no and s.dimension_class = c.dimension_class
        where not (c.dimension_class = 'industry_naics_group'
                   and c.stub_label in (select published_label from {{ ref('ised_industry_aggregate_codes') }})))
where (select count(*) from {{ ref('fact_ised_financing_context') }})
   <> (select count(*) from {{ ref('stg_ised__financing_context') }} c join {{ ref('ised_segment_scope') }} s
          on s.table_no = c.table_no and s.dimension_class = c.dimension_class
        where not (c.dimension_class = 'industry_naics_group'
                   and c.stub_label in (select published_label from {{ ref('ised_industry_aggregate_codes') }})))
union all
select 'ISED rows without a resolved segment key', count(*), 0
from {{ ref('fact_ised_financing_context') }} where segment_key is null having count(*) > 0
union all
select 'dim_industry join duplicates sector rows',
       (select count(*) from {{ ref('fact_cma_sector_composition_snapshot') }} f
          join {{ ref('dim_industry') }} d on d.industry_system = 'NAICS2022_1097' and d.industry_code = f.naics_sector),
       (select count(*) from {{ ref('fact_cma_sector_composition_snapshot') }})
where (select count(*) from {{ ref('fact_cma_sector_composition_snapshot') }} f
          join {{ ref('dim_industry') }} d on d.industry_system = 'NAICS2022_1097' and d.industry_code = f.naics_sector)
   <> (select count(*) from {{ ref('fact_cma_sector_composition_snapshot') }})
