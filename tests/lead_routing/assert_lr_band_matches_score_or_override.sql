{{ config(tags=['lead_routing']) }}
-- Required test: valid overrides. Without an override the band comes from the declared score bands; with one it follows
-- the declared action exactly, and every override carries a reason.
select p.decision_id
from {{ ref('mart_lead_priority') }} p
left join {{ ref('lead_priority_band_rules') }} b
       on p.base_priority_score >= b.min_score_inclusive and p.base_priority_score < b.max_score_exclusive
where
    (p.override_rule_id is null and p.recommended_priority_band is distinct from b.band_code)
 or (p.override_action = 'force_band'    and p.recommended_priority_band is distinct from
        (select forced_band from {{ ref('lead_priority_override_rules') }} o where o.rule_id = p.override_rule_id))
 or (p.override_action = 'band_floor'    and p.recommended_priority_band is distinct from
        (select band_floor from {{ ref('lead_priority_override_rules') }} o where o.rule_id = p.override_rule_id))
 or (p.override_action = 'band_cap'      and p.recommended_priority_band is distinct from
        (select band_cap   from {{ ref('lead_priority_override_rules') }} o where o.rule_id = p.override_rule_id))
 or (p.override_action = 'suppress_score' and (p.recommended_priority_band is not null or p.recommended_priority_score is not null))
 or (p.override_rule_id is not null and (p.override_reason is null or p.override_reason = ''))
