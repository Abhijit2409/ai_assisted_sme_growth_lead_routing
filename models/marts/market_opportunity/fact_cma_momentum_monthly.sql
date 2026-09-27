-- Enterprise dynamics (StatCan 0722 only): one row per CMA x month x size scope x industry member x measure.
-- Published rows keep their StatCan status; derived rows (net formation and rates) carry their own unit and formula.
with published as (
    select p.cma_code_sgc2016, p.ref_month, p.size_member_id, p.industry_member_id, mm.measure, mm.published_label as measure_label,
           'published' as value_origin, cast(null as varchar) as formula,
           case when p.status_raw in ('', 'E') then p.value_num end as value,
           case p.status_raw when '' then 'published' when 'E' then 'published_E' when 'x' then 'suppressed_x'
                when '..' then 'not_available_dotdot' else 'unexpected_status' end as value_state,
           p.status_raw as status, p.vector
    from {{ ref('int_market__momentum_published') }} as p
    join {{ ref('statcan_0722_measure_members') }} as mm on cast(mm.measure_member_id as integer) = p.measure_member_id
),
derived as (
    select cma_code_sgc2016, ref_month, size_member_id, industry_member_id, measure, measure_label, 'derived', formula, value,
           value_state, cast(null as varchar), cast(null as varchar)
    from {{ ref('int_market__momentum_derived') }}
),
unioned as (
    select * from published
    union all
    select * from derived
),
labelled as (
    select
        '{{ var("statcan_0722_source_id") }}'                                     as source_id,
        b.cma_code_sgc2016,
        b.cma_code_sgc2021,
        u.ref_month,
        s.size_band_key,
        cast(u.industry_member_id as varchar)                                     as industry_code_0722,
        u.measure,
        u.measure_label,
        u.value_origin,
        u.formula,
        u.value,
        u.value_state in ('published', 'published_E', 'derived', 'derived_E')     as value_available,
        u.value_state,
        u.status,
        u.value_state in ('published_E', 'derived_E')                             as caution_flag,
        case when u.measure in ('active', 'openings', 'continuing', 'closures') then 'enterprise'
             when u.measure = 'net_formation' then 'enterprise (net count)'
             else 'ratio to active enterprises at t-1' end                        as statistical_unit,
        case s.size_band_key when 'TOTAL' then 'all-employer-business context' when '1-4' then 'small-business measure'
             when '5-19' then 'small-business measure' when '500-plus' then 'outside SME scope (500+)'
             else 'SME-specific measure' end                                      as scope_class,
        '0722 monthly, seasonally adjusted'                                       as period_label,
        u.ref_month between '{{ var("pandemic_window_start") }}' and '{{ var("pandemic_window_end") }}' as pandemic_window_flag,
        true                                                                      as seasonally_adjusted,
        b.boundary_change_flag,
        u.vector
    from unioned as u
    join {{ ref('int_market__cma_vintage_alignment') }} as b on b.cma_code_sgc2016 = u.cma_code_sgc2016
    join {{ ref('statcan_0722_size_members') }} as s on cast(s.size_member_id as integer) = u.size_member_id
)
select *,
    case
        when industry_code_0722 = '1' and size_band_key = 'TOTAL' then case measure when 'active' then 'M01' when 'openings' then 'M02'
             when 'closures' then 'M03' when 'net_formation' then 'M04' when 'opening_rate' then 'M05' when 'closure_rate' then 'M06'
             when 'net_formation_rate' then 'M07' end
        when industry_code_0722 = '1' and size_band_key in ('1-4', '5-19') then case measure when 'active' then 'M09'
             when 'opening_rate' then 'M10' end
        when industry_code_0722 <> '1' and size_band_key = 'TOTAL' and measure <> 'continuing' then 'M11'
    end as metric_id
from labelled
order by cma_code_sgc2016, ref_month, size_band_key, industry_code_0722, measure
