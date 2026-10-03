-- Explainable priority components.
-- Grain: one row per decision_id x component_id (ten components per scoreable decision; primary key (decision_id, component_id)).
-- Every row preserves the raw value, the transformation, the normalized 0-100 value, the direction, the pillar and component
-- weights, the resulting contribution and whether the declared neutral value was used instead of an observed value.
-- Only decisions whose priority eligibility is score_eligible appear here.
-- As-of rule: inherited from int_lead_routing__priority_features_as_of. No outcome, later event or ground truth is read.
with eligible as (
    select f.*
    from {{ ref('int_lead_routing__priority_features_as_of') }} f
    join {{ ref('int_lead_routing__priority_eligibility') }} e using (decision_id)
    where e.is_scoreable
),

weights as (
    select
        max(case when level = 'pillar' and pillar = 'business_value'       then weight end) as w_bv,
        max(case when level = 'pillar' and pillar = 'operational_urgency'  then weight end) as w_ou,
        max(case when component_id = 'BV01_CHANNEL'       then weight end) as w_bv01,
        max(case when component_id = 'BV02_INTENT'        then weight end) as w_bv02,
        max(case when component_id = 'BV03_SIZE'          then weight end) as w_bv03,
        max(case when component_id = 'BV04_INDUSTRY'      then weight end) as w_bv04,
        max(case when component_id = 'BV05_MARKET'        then weight end) as w_bv05,
        max(case when component_id = 'BV06_COMPLETENESS'  then weight end) as w_bv06,
        max(case when component_id = 'OU01_AGE'           then weight end) as w_ou01,
        max(case when component_id = 'OU02_INTAKE_SLA'    then weight end) as w_ou02,
        max(case when component_id = 'OU03_OWNERSHIP_SLA' then weight end) as w_ou03,
        max(case when component_id = 'OU04_ENGAGEMENT'    then weight end) as w_ou04
    from {{ ref('lead_priority_weight_config') }}
),

long as (
    select e.decision_id, 'BV01_CHANNEL' as component_id,
           coalesce(e.first_channel_code, 'unknown')                          as raw_value_text,
           cast(null as double)                                               as raw_value_numeric,
           'category map'                                                     as transformation,
           case e.first_channel_code when 'CH-PTR' then 90.0 when 'CH-BRN' then 85.0 when 'CH-RMP' then 75.0
                when 'CH-MKT' then 50.0 when 'CH-DIG' then 40.0 else 40.0 end as normalized_value,
           e.first_channel_code is null                                       as used_neutral_default
    from eligible e
    union all
    select e.decision_id, 'BV02_INTENT',
           coalesce(e.stated_product_interest, 'not stated'), null, 'category map',
           case e.stated_product_interest when 'credit' then 95.0 when 'cash management' then 75.0
                when 'merchant services' then 65.0 when 'operating account' then 60.0 else 35.0 end,
           e.stated_product_interest is null or e.stated_product_interest = 'not stated'
    from eligible e
    union all
    select e.decision_id, 'BV03_SIZE',
           coalesce(e.employee_band, 'unknown'), null, 'category map',
           case e.employee_band when '100-499' then 95.0 when '20-99' then 85.0 when '5-19' then 60.0
                when '1-4' then 40.0 when '500-plus' then 50.0 else 35.0 end,
           e.employee_band is null or e.employee_band = 'unknown'
    from eligible e
    union all
    select e.decision_id, 'BV04_INDUSTRY',
           e.specialist_requirement_level, null, 'specialist requirement level from specialist_rule_catalog',
           case e.specialist_requirement_level when 'mandatory' then 80.0 when 'preferred' then 70.0
                when 'standard' then 55.0 when 'unclassified' then 35.0 else 0.0 end,
           e.specialist_requirement_level = 'unclassified'
    from eligible e
    union all
    select e.decision_id, 'BV05_MARKET',
           e.market_context_treatment, e.market_opportunity_score, 'identity, clipped to 0-100',
           case when e.market_opportunity_score is not null
                then least(100.0, greatest(0.0, e.market_opportunity_score)) else 50.0 end,
           e.market_opportunity_score is null
    from eligible e
    union all
    select e.decision_id, 'BV06_COMPLETENESS',
           cast(e.completeness_items_present as varchar) || ' of 6',
           cast(e.completeness_items_present as double), '100 * items present / 6',
           100.0 * e.completeness_items_present / 6.0, false
    from eligible e
    union all
    select e.decision_id, 'OU01_AGE',
           null, e.lead_age_hours_at_decision, 'hours between captured_at and as_of_timestamp',
           case when e.lead_age_hours_at_decision <   1 then 10.0
                when e.lead_age_hours_at_decision <   4 then 25.0
                when e.lead_age_hours_at_decision <  24 then 45.0
                when e.lead_age_hours_at_decision <  72 then 65.0
                when e.lead_age_hours_at_decision < 168 then 85.0
                else 100.0 end, false
    from eligible e
    union all
    select e.decision_id, 'OU02_INTAKE_SLA',
           case when e.intake_clock_in_flight then 'clock in flight' else 'no clock in flight' end,
           e.intake_sla_pressure, 'share of the SLA target consumed at the decision instant',
           case when not e.intake_clock_in_flight            then 30.0
                when e.intake_sla_pressure < 0.25            then 30.0
                when e.intake_sla_pressure < 0.50            then 50.0
                when e.intake_sla_pressure < 0.75            then 70.0
                when e.intake_sla_pressure < 1.00            then 85.0
                else 100.0 end,
           not e.intake_clock_in_flight
    from eligible e
    union all
    select e.decision_id, 'OU03_OWNERSHIP_SLA',
           case when e.ownership_clock_in_flight then 'clock in flight' else 'no clock in flight' end,
           e.ownership_sla_pressure, 'share of the SLA target consumed at the decision instant',
           case when not e.ownership_clock_in_flight         then 30.0
                when e.ownership_sla_pressure < 0.25         then 30.0
                when e.ownership_sla_pressure < 0.50         then 50.0
                when e.ownership_sla_pressure < 0.75         then 70.0
                when e.ownership_sla_pressure < 1.00         then 85.0
                else 100.0 end,
           not e.ownership_clock_in_flight
    from eligible e
    union all
    select e.decision_id, 'OU04_ENGAGEMENT',
           e.pre_decision_engagement_state, cast(e.contact_attempts_before_decision as double),
           'state derived from activities strictly before as_of',
           case e.pre_decision_engagement_state when 'two_way_contact' then 100.0
                when 'three_or_more_attempts_no_contact' then 85.0
                when 'one_or_two_attempts_no_contact' then 60.0 else 30.0 end,
           e.pre_decision_engagement_state = 'no_activity'
    from eligible e
)

select
    l.decision_id,
    l.component_id,
    r.pillar,
    r.component_name,
    l.raw_value_text,
    l.raw_value_numeric,
    l.transformation,
    l.normalized_value,
    r.direction,
    l.used_neutral_default,
    r.neutral_value                                            as declared_neutral_value,
    case r.pillar when 'business_value' then w.w_bv else w.w_ou end as pillar_weight,
    case l.component_id
        when 'BV01_CHANNEL'       then w.w_bv01 when 'BV02_INTENT'        then w.w_bv02
        when 'BV03_SIZE'          then w.w_bv03 when 'BV04_INDUSTRY'      then w.w_bv04
        when 'BV05_MARKET'        then w.w_bv05 when 'BV06_COMPLETENESS'  then w.w_bv06
        when 'OU01_AGE'           then w.w_ou01 when 'OU02_INTAKE_SLA'    then w.w_ou02
        when 'OU03_OWNERSHIP_SLA' then w.w_ou03 else w.w_ou04 end  as component_weight,
    (case r.pillar when 'business_value' then w.w_bv else w.w_ou end)
      * (case l.component_id
            when 'BV01_CHANNEL'       then w.w_bv01 when 'BV02_INTENT'        then w.w_bv02
            when 'BV03_SIZE'          then w.w_bv03 when 'BV04_INDUSTRY'      then w.w_bv04
            when 'BV05_MARKET'        then w.w_bv05 when 'BV06_COMPLETENESS'  then w.w_bv06
            when 'OU01_AGE'           then w.w_ou01 when 'OU02_INTAKE_SLA'    then w.w_ou02
            when 'OU03_OWNERSHIP_SLA' then w.w_ou03 else w.w_ou04 end)        as effective_weight,
    (case r.pillar when 'business_value' then w.w_bv else w.w_ou end)
      * (case l.component_id
            when 'BV01_CHANNEL'       then w.w_bv01 when 'BV02_INTENT'        then w.w_bv02
            when 'BV03_SIZE'          then w.w_bv03 when 'BV04_INDUSTRY'      then w.w_bv04
            when 'BV05_MARKET'        then w.w_bv05 when 'BV06_COMPLETENESS'  then w.w_bv06
            when 'OU01_AGE'           then w.w_ou01 when 'OU02_INTAKE_SLA'    then w.w_ou02
            when 'OU03_OWNERSHIP_SLA' then w.w_ou03 else w.w_ou04 end)
      * l.normalized_value                                                    as contribution,
    '{{ var("lead_routing_rule_version") }}'                                  as rule_version
from long l
cross join weights w
join {{ ref('lead_priority_component_registry') }} r on r.component_id = l.component_id
