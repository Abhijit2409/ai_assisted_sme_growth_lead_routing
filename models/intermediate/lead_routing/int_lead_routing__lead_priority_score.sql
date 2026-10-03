-- Final recommended priority score, band and override for every decision.
-- Grain: one row per decision_id (primary key). Ineligible decisions carry a NULL score and band with their eligibility reason.
-- The score is rebuilt from the ten published contributions in a fixed component order, so the arithmetic is identical on
-- every build regardless of thread count or aggregation order. The source priority_band field on crm_lead is never modified.
-- As-of rule: inherited from int_lead_routing__priority_features_as_of. No outcome, later event or ground truth is read.
with thresholds as (
    select
        max(case when parameter = 'priority_rounding_decimals' then cast(value as integer) end) as score_decimals,
        max(case when parameter = 'imminent_breach_pressure'   then cast(value as double) end)  as imminent_breach_pressure
    from {{ ref('routing_evaluation_thresholds') }}
),

pivoted as (
    select
        decision_id,
        max(case when component_id = 'BV01_CHANNEL'       then contribution end) as c_bv01,
        max(case when component_id = 'BV02_INTENT'        then contribution end) as c_bv02,
        max(case when component_id = 'BV03_SIZE'          then contribution end) as c_bv03,
        max(case when component_id = 'BV04_INDUSTRY'      then contribution end) as c_bv04,
        max(case when component_id = 'BV05_MARKET'        then contribution end) as c_bv05,
        max(case when component_id = 'BV06_COMPLETENESS'  then contribution end) as c_bv06,
        max(case when component_id = 'OU01_AGE'           then contribution end) as c_ou01,
        max(case when component_id = 'OU02_INTAKE_SLA'    then contribution end) as c_ou02,
        max(case when component_id = 'OU03_OWNERSHIP_SLA' then contribution end) as c_ou03,
        max(case when component_id = 'OU04_ENGAGEMENT'    then contribution end) as c_ou04,
        max(case when component_id = 'BV05_MARKET' then normalized_value end)    as market_normalized_value,
        max(case when component_id = 'BV05_MARKET' then used_neutral_default end) as market_used_neutral_default,
        count(*)                                                                  as component_count
    from {{ ref('int_lead_routing__priority_components') }}
    group by 1
),

scored as (
    select
        p.decision_id,
        round(p.c_bv01 + p.c_bv02 + p.c_bv03 + p.c_bv04 + p.c_bv05 + p.c_bv06, t.score_decimals)
                                                                            as business_value_contribution,
        round(p.c_ou01 + p.c_ou02 + p.c_ou03 + p.c_ou04, t.score_decimals)  as operational_urgency_contribution,
        round(((((((((p.c_bv01 + p.c_bv02) + p.c_bv03) + p.c_bv04) + p.c_bv05) + p.c_bv06)
                 + p.c_ou01) + p.c_ou02) + p.c_ou03) + p.c_ou04, t.score_decimals) as base_priority_score,
        p.market_normalized_value,
        p.market_used_neutral_default,
        p.component_count
    from pivoted p
    cross join thresholds t
),

banded as (
    select
        s.*,
        (select b.band_code from {{ ref('lead_priority_band_rules') }} b
          where s.base_priority_score >= b.min_score_inclusive
            and s.base_priority_score <  b.max_score_exclusive) as base_priority_band
    from scored s
),

override_applied as (
    select
        f.decision_id,
        e.priority_eligibility,
        e.is_scoreable,
        b.base_priority_score,
        b.base_priority_band,
        b.business_value_contribution,
        b.operational_urgency_contribution,
        b.market_normalized_value,
        b.market_used_neutral_default,
        b.component_count,
        -- ordered override evaluation: first matching rule wins
        case
            when not e.is_scoreable and e.priority_eligibility = 'duplicate_review'  then 'POV-04'
            when not e.is_scoreable and e.priority_eligibility = 'data_remediation'  then 'POV-05'
            when not e.is_scoreable                                                   then 'POV-07'
            when f.imminent_sla_breach_at_decision                                    then 'POV-01'
            when f.escalation_clock_in_flight                                         then 'POV-02'
            when f.first_channel_code in ('CH-BRN', 'CH-PTR')
                 and f.consent_to_contact_flag
                 and f.stated_product_interest = 'credit'
                 and coalesce(b.base_priority_band, 'P3') <> 'P1'                     then 'POV-03'
            when f.lead_status_at_decision = 'NURTURING'
                 and coalesce(b.base_priority_band, 'P3') <> 'P3'                     then 'POV-06'
        end                                                                           as override_rule_id,
        f.lead_id, f.as_of_timestamp, f.as_of_date, f.decision_trigger, f.decision_type,
        f.lead_status_at_decision, f.decision_sequence_for_lead, f.is_capacity_warm_up_period,
        f.market_context_treatment, f.market_evidence_flag, f.evidence_tier,
        f.imminent_sla_breach_at_decision, f.escalation_clock_in_flight, f.max_sla_pressure_at_decision,
        f.rule_version
    from {{ ref('int_lead_routing__priority_features_as_of') }} f
    join {{ ref('int_lead_routing__priority_eligibility') }} e on e.decision_id = f.decision_id
    left join banded b on b.decision_id = f.decision_id
)

select
    o.decision_id,
    o.lead_id,
    o.as_of_timestamp,
    o.as_of_date,
    o.decision_trigger,
    o.decision_type,
    o.lead_status_at_decision,
    o.decision_sequence_for_lead,
    o.is_capacity_warm_up_period,
    o.priority_eligibility,
    o.is_scoreable,
    o.component_count,
    o.business_value_contribution,
    o.operational_urgency_contribution,
    o.base_priority_score,
    o.base_priority_band,
    o.override_rule_id,
    r.override_action,
    case
        when o.override_rule_id is null                       then o.base_priority_band
        when r.override_action = 'suppress_score'             then cast(null as varchar)
        when r.override_action = 'force_band'                 then r.forced_band
        when r.override_action = 'band_floor'                 then r.band_floor
        when r.override_action = 'band_cap'                   then r.band_cap
    end                                                       as recommended_priority_band,
    case when o.override_rule_id is not null and r.override_action = 'suppress_score'
         then cast(null as double) else o.base_priority_score end as recommended_priority_score,
    coalesce(r.trigger_condition, 'No override rule applied; the band comes directly from the declared score bands.')
                                                              as override_reason,
    o.market_context_treatment,
    o.market_evidence_flag,
    o.evidence_tier,
    o.market_normalized_value,
    o.market_used_neutral_default,
    o.imminent_sla_breach_at_decision,
    o.escalation_clock_in_flight,
    o.max_sla_pressure_at_decision,
    o.rule_version
from override_applied o
left join {{ ref('lead_priority_override_rules') }} r on r.rule_id = o.override_rule_id
