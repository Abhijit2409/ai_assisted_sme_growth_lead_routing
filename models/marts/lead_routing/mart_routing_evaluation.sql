-- Three-way comparison of routing policies over the same decision population.
-- Grain: one row per policy x metric x period scope. Primary key: (policy, metric_id, period_scope).
--
-- The three policies compared:
--   historical                 - the synthetic historical assignment recorded in the routing log. A simulated policy
--                                record, not a correct label.
--   static_diagnostic          - mart_routing_recommendation_static: the declared rules applied independently at every
--                                decision, with no capacity consumed. A diagnostic of rule fit, not an executable plan.
--   capacity_aware_operational - mart_routing_recommendation_operational: the declared rules applied in the declared
--                                processing order against a simulated RM capacity ledger. The operational answer.
--
-- Interpretation, and the only interpretation supported by this model:
--   * rule-adherence comparison      - how often each policy satisfies the declared territory, specialist, language
--                                      and capacity rules;
--   * routing-policy counterfactual  - what the declared rules would have produced at the same decision instants;
--   * operational exposure           - how much capacity and concentration exposure each policy carries;
--   * assignment-quality proxy       - rule fit as a proxy for assignment quality, nothing more.
--
-- Nothing here is evidence of a causal improvement in conversion, revenue, profitability or customer experience.
--
-- A standing limitation of both recommendation policies: the capacity snapshots are the ones the historical policy
-- produced. The operational ledger therefore binds within a snapshot window, but each new observed snapshot replaces
-- the basis, so load from this policy's own earlier recommendations does not carry across snapshot boundaries.
-- Concentration measured over a full year is therefore still driven by preference, not by capacity.
-- Fictional bank, hypothetical rules, entirely synthetic data.
with warm_up as (
    select cast(value as date) as warm_up_end
    from {{ ref('routing_evaluation_thresholds') }} where parameter = 'warm_up_period_end_exclusive'
),

hard_limit as (
    select cast(value as double) as ratio
    from {{ ref('routing_evaluation_thresholds') }} where parameter = 'capacity_hard_limit_ratio'
),

static_rec as (select * from {{ ref('mart_routing_recommendation_static') }}),
op_rec     as (select * from {{ ref('mart_routing_recommendation_operational') }}),
cand       as (select * from {{ ref('mart_routing_candidate') }}),

-- the historical language-match flag lives on the routing log, not on the recommendation mart
hist_lang as (
    select 'DEC-' || routing_decision_id as decision_id, language_match_flag
    from {{ ref('stg_crm__routing_decision') }}
),

-- one row per decision per policy, with the rule outcomes expressed identically for all three
policy_rows as (
    select
        'historical'                                                  as policy,
        s.decision_id, s.as_of_date, s.decision_trigger, s.decision_type,
        s.specialist_requirement_level, s.stated_service_language,
        s.historical_selected_rm_id                                   as assigned_rm_id,
        (s.historical_selected_rm_id is null)                         as is_queued,
        s.historical_queue_code                                       as queue_code,
        s.historical_rm_covers_territory                              as territory_ok,
        s.historical_rm_holds_primary_specialization                  as mandatory_specialist_ok,
        s.historical_rm_holds_any_specialization                      as preferred_specialist_ok,
        hl.language_match_flag                                        as language_ok,
        s.historical_capacity_utilization                             as utilization_after
    from static_rec s
    left join hist_lang hl on hl.decision_id = s.decision_id

    union all

    select
        'static_diagnostic',
        s.decision_id, s.as_of_date, s.decision_trigger, s.decision_type,
        s.specialist_requirement_level, s.stated_service_language,
        s.recommended_rm_id,
        (s.recommendation_outcome = 'queue'),
        s.recommended_queue_code,
        (s.recommended_territory_coverage_role is not null),
        s.recommended_rm_holds_primary_specialization,
        s.recommended_rm_holds_any_specialization,
        -- REL-11 is a mandatory eligibility rule, so any recommended RM already supports a stated language
        (s.recommended_rm_id is not null),
        s.recommended_utilization_ratio
    from static_rec s

    union all

    select
        'capacity_aware_operational',
        o.decision_id, o.as_of_date, o.decision_trigger, o.decision_type,
        o.specialist_requirement_level, o.stated_service_language,
        o.recommended_rm_id,
        (o.recommendation_status = 'queued'),
        o.queue_code,
        (o.territory_eligibility_result in ('covers lead territory', 'national specialist pool fallback')),
        c.holds_primary_specialization,
        c.holds_any_specialization,
        (o.recommended_rm_id is not null),
        o.simulated_post_assignment_utilization
    from op_rec o
    left join cand c on c.decision_id = o.decision_id and c.rm_id = o.recommended_rm_id
),

scoped as (
    select p.*, 'full_period' as period_scope from policy_rows p
    union all
    select p.*, 'excluding_warm_up' from policy_rows p cross join warm_up w where p.as_of_date >= w.warm_up_end
),

-- RM workload per policy, reindexed over every RM so an RM with no work counts as a zero
rm_universe as (select rm_id from {{ ref('dim_relationship_manager') }}),

policy_scopes as (select distinct policy, period_scope from scoped),

rm_load as (
    select ps.policy, ps.period_scope, u.rm_id, count(a.decision_id) as n
    from policy_scopes ps
    cross join rm_universe u
    left join scoped a on a.policy = ps.policy and a.period_scope = ps.period_scope and a.assigned_rm_id = u.rm_id
    group by 1, 2, 3
),

rm_load_ranked as (
    select l.*,
           ntile(10) over (partition by l.policy, l.period_scope order by l.n desc, l.rm_id)   as load_decile,
           row_number() over (partition by l.policy, l.period_scope order by l.n asc, l.rm_id) as asc_rank,
           count(*) over (partition by l.policy, l.period_scope)                               as rm_count,
           sum(l.n) over (partition by l.policy, l.period_scope)                               as policy_total
    from rm_load l
),

concentration as (
    select policy, period_scope,
           max(rm_count)                                        as rms_total,
           sum(case when n = 0 then 1 else 0 end)               as rms_with_no_recommendations,
           max(n)                                               as max_to_one_rm,
           sum(case when load_decile = 1 then n else 0 end)     as top_decile_n,
           max(policy_total)                                    as policy_total,
           -- Gini over the RM workload distribution, from the ascending rank of each RM
           case when max(policy_total) > 0
                then sum((2 * asc_rank - rm_count - 1) * n)
                     / (max(rm_count)::double * max(policy_total))
           end                                                  as gini
    from rm_load_ranked group by 1, 2
),

metrics as (
    select
        policy, period_scope,
        count(*)                                                                    as decisions,
        count(*) filter (where assigned_rm_id is not null)                          as assigned,
        count(*) filter (where is_queued)                                           as queued,
        count(distinct assigned_rm_id)                                              as distinct_rms,
        count(*) filter (where assigned_rm_id is not null and territory_ok)         as territory_met,
        count(*) filter (where specialist_requirement_level = 'mandatory')          as mandatory_demand,
        count(*) filter (where specialist_requirement_level = 'mandatory' and mandatory_specialist_ok)
                                                                                    as mandatory_met,
        count(*) filter (where specialist_requirement_level = 'mandatory' and assigned_rm_id is not null)
                                                                                    as mandatory_assigned,
        count(*) filter (where specialist_requirement_level = 'mandatory' and assigned_rm_id is not null
                           and mandatory_specialist_ok)                             as mandatory_assigned_met,
        count(*) filter (where specialist_requirement_level = 'preferred')          as preferred_demand,
        count(*) filter (where specialist_requirement_level = 'preferred' and preferred_specialist_ok)
                                                                                    as preferred_met,
        count(*) filter (where stated_service_language in ('en', 'fr'))             as language_demand,
        count(*) filter (where stated_service_language in ('en', 'fr') and assigned_rm_id is not null
                           and language_ok)                                         as language_met,
        count(*) filter (where assigned_rm_id is not null
                           and utilization_after >= (select ratio from hard_limit)) as hard_capacity_breaches
    from scoped group by 1, 2
),

long as (
    select policy, period_scope, 'assignment_rate' as metric_id,
           'Share of decisions that produced an RM assignment' as metric_name,
           cast(assigned as double) as numerator, cast(decisions as double) as denominator,
           'rule-adherence comparison' as interpretation from metrics
    union all
    select policy, period_scope, 'queue_rate', 'Share of decisions held in a controlled queue',
           cast(queued as double), cast(decisions as double), 'operational exposure' from metrics
    union all
    select policy, period_scope, 'territory_rule_compliance', 'Assignments where the RM covers the lead territory',
           cast(territory_met as double), cast(assigned as double), 'rule-adherence comparison' from metrics
    union all
    select policy, period_scope, 'mandatory_specialist_fulfilment',
           'Mandatory-specialist decisions that reached a specialist, over all such demand',
           cast(mandatory_met as double), cast(mandatory_demand as double), 'rule-adherence comparison' from metrics
    union all
    select policy, period_scope, 'mandatory_specialist_compliance',
           'Of the mandatory-specialist decisions that were assigned, the share whose RM holds the specialization',
           cast(mandatory_assigned_met as double), cast(mandatory_assigned as double),
           'rule-adherence comparison' from metrics
    union all
    select policy, period_scope, 'preferred_specialist_fulfilment',
           'Preferred-specialist decisions where the assigned RM holds a relevant specialization',
           cast(preferred_met as double), cast(preferred_demand as double), 'assignment-quality proxy' from metrics
    union all
    select policy, period_scope, 'language_fulfilment',
           'Decisions with an explicitly stated language where the assigned RM supports it',
           cast(language_met as double), cast(language_demand as double), 'rule-adherence comparison' from metrics
    union all
    select policy, period_scope, 'hard_capacity_breaches',
           'Assignments made to an RM at or above the declared hard capacity limit',
           cast(hard_capacity_breaches as double), cast(assigned as double), 'operational exposure' from metrics
    union all
    select policy, period_scope, 'distinct_rms_receiving_work',
           'Distinct RMs that received at least one assignment',
           cast(distinct_rms as double), 1.0, 'operational exposure' from metrics
    union all
    select c.policy, c.period_scope, 'rms_with_no_recommendations', 'RMs that received no assignment at all',
           cast(c.rms_with_no_recommendations as double), cast(c.rms_total as double), 'operational exposure'
    from concentration c
    union all
    select c.policy, c.period_scope, 'max_recommendations_to_one_rm',
           'Largest number of assignments given to a single RM',
           cast(c.max_to_one_rm as double), 1.0, 'operational exposure' from concentration c
    union all
    select c.policy, c.period_scope, 'mean_recommendations_per_rm',
           'Mean assignments per RM across the whole RM population',
           cast(c.policy_total as double), cast(c.rms_total as double), 'operational exposure' from concentration c
    union all
    select c.policy, c.period_scope, 'top_decile_share',
           'Share of assignments landing on the busiest decile of RMs',
           cast(c.top_decile_n as double), cast(c.policy_total as double), 'operational exposure' from concentration c
    union all
    select c.policy, c.period_scope, 'workload_gini',
           'Gini coefficient of assignments across all RMs (0 perfectly even, 1 fully concentrated)',
           c.gini, 1.0, 'operational exposure' from concentration c
),

queue_reasons as (
    select policy, period_scope,
           'queue_reason_' || queue_code                as metric_id,
           'Decisions held in queue ' || queue_code     as metric_name,
           cast(count(*) as double)                     as numerator,
           cast(max(total) as double)                   as denominator,
           'operational exposure'                       as interpretation
    from (select s.*, count(*) over (partition by s.policy, s.period_scope) as total from scoped s)
    where queue_code is not null
    group by 1, 2, 3, 4
)

select
    l.policy,
    l.metric_id,
    l.period_scope,
    l.metric_name,
    l.interpretation,
    l.numerator,
    l.denominator,
    case when l.denominator > 0 then l.numerator / l.denominator end as metric_value,
    '{{ var("lead_routing_rule_version") }}'                          as rule_version
from (select * from long union all select * from queue_reasons) l
