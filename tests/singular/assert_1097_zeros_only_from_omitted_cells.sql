-- 1097 publishes no zeros; a zero in the facts can only come from omitted cells inside a listed CMA, which are
-- counted on every row. The snapshot label must match the source reference date.
select 'published zero in 1097' as issue, count(*) as rows_failing from {{ ref('stg_statcan__business_locations') }}
where locations = 0 having count(*) > 0
union all
select 'component cells do not add up', count(*) from {{ ref('fact_cma_market_scale_snapshot') }}
where n_published_cells + n_zero_by_reconciliation_cells <> n_component_cells having count(*) > 0
union all
select 'all-sizes total is not a published cell', count(*) from {{ ref('fact_cma_market_scale_snapshot') }}
where size_scope = 'all_sizes' and n_published_cells <> 1 having count(*) > 0
union all
select 'source reference date differs from the snapshot label', count(*) from {{ ref('stg_statcan__business_locations') }}
where ref_date <> '{{ var("source_ref_date_1097") }}' having count(*) > 0
