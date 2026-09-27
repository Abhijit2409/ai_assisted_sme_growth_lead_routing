-- Grain: one row per CMA x size member x industry member x month where openings or closures are published.
-- Brings each month's openings and closures next to the same series' active enterprises in the previous
-- calendar month (the 0722 denominator; never a 1097 count). Input flags record availability and E status.
with pivoted as (
    select cma_code_sgc2016, ref_month, size_member_id, industry_member_id,
           max(case when measure_member_id = 1 then value_num end)   as act,
           max(case when measure_member_id = 1 then status_raw end)  as act_s,
           bool_or(measure_member_id = 1)                            as has_act,
           max(case when measure_member_id = 2 then value_num end)   as opn,
           max(case when measure_member_id = 2 then status_raw end)  as opn_s,
           bool_or(measure_member_id = 2)                            as has_opn,
           max(case when measure_member_id = 4 then value_num end)   as cls,
           max(case when measure_member_id = 4 then status_raw end)  as cls_s,
           bool_or(measure_member_id = 4)                            as has_cls
    from {{ ref('int_market__momentum_published') }}
    group by all
),
with_previous as (
    select cur.*, prev.act as pact, prev.act_s as pact_s, coalesce(prev.has_act, false) as has_pact
    from pivoted as cur
    left join pivoted as prev
      on prev.cma_code_sgc2016 = cur.cma_code_sgc2016 and prev.size_member_id = cur.size_member_id
     and prev.industry_member_id = cur.industry_member_id
     and prev.ref_month = strftime(strptime(cur.ref_month || '-01', '%Y-%m-%d') - interval 1 month, '%Y-%m')
    where cur.has_opn or cur.has_cls
)
select *,
       coalesce(opn_s in ('', 'E') and opn is not null, false)                         as opn_ok,
       coalesce(cls_s in ('', 'E') and cls is not null, false)                         as cls_ok,
       coalesce(has_pact and pact_s in ('', 'E') and pact is not null and pact > 0, false) as pact_ok,
       coalesce(opn_s = 'E', false)  as opn_e,
       coalesce(cls_s = 'E', false)  as cls_e,
       coalesce(pact_s = 'E', false) as pact_e
from with_previous
