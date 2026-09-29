{{ config(tags=['opportunity']) }}
-- Public aggregates are never represented as prospects, leads or customers: no such column exists in the opportunity
-- marts, and every row carries the external-signal interpretation note.
select 'prospect-like column' as issue, table_name || '.' || column_name as detail from information_schema.columns
where table_schema = 'analytics' and table_name like 'mart_cma_%'
  and (column_name ilike '%lead%' or column_name ilike '%prospect%' or column_name ilike '%customer%' or column_name ilike '%revenue%'
       or column_name ilike '%conversion%' or column_name ilike '%probability%')
union all
select 'row without interpretation note', cma_code_sgc2021 from {{ ref('mart_cma_opportunity_framework') }} where interpretation_note not like 'External market signal%'
union all
select 'scale-only row without interpretation note', cma_code_sgc2021 from {{ ref('mart_cma_scale_only_context') }}
where interpretation_note not like 'External market context%'
