{{ config(tags=['opportunity']) }}
-- Only the six retained indicators are registered; only registered components are normalized; only core-score
-- components carry weight in the balanced, scale-led and growth-led scenarios; C10 carries weight only in the stability
-- scenarios; no ISED or supporting, rejected or unresolved indicator can enter a score.
select 'unapproved indicator in the registry' as issue, indicator_id as detail from {{ ref('opportunity_indicator_registry') }}
where indicator_id not in ('C01', 'C03', 'C05', 'C09', 'C10', 'C15')
union all
select 'ISED source in the registry', component_id from {{ ref('opportunity_indicator_registry') }}
where source_model ilike '%ised%' or source_columns ilike '%ised%'
union all
select 'normalized component not registered', component_id from {{ ref('int_opportunity__normalized_indicators') }}
where component_id not in (select component_id from {{ ref('opportunity_indicator_registry') }})
union all
select 'core component with a non-core role', component_id from {{ ref('opportunity_indicator_registry') }}
where component_id in ('C01_LOG_SCALE', 'C03_OPENING_RATE', 'C05_NET_FORMATION_RATE', 'C09_NET_FORMATION_TREND', 'C15_SHARE_20_99') and framework_role <> 'core score'
union all
select 'weighted pillar outside the approved structure', w.scenario_id || ':' || w.pillar from {{ ref('opportunity_weight_scenarios') }} as w
where w.pillar_weight > 0 and (
      (w.scenario_id in ('balanced', 'scale_led', 'growth_led') and w.pillar not in ('scale', 'momentum', 'dynamism', 'commercial_fit'))
   or (w.scenario_id = 'stability_aware' and w.pillar = 'stability_unadjusted')
   or (w.scenario_id = 'volatility_penalty_unadjusted' and w.pillar = 'stability'))
union all
select 'pillar without a registered component', pillar from {{ ref('opportunity_weight_scenarios') }}
where pillar not in (select pillar from {{ ref('opportunity_indicator_registry') }})
