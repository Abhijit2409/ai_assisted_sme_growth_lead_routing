-- Which prospects need action? One row per currently actionable lead, with the action, the owner of that action
-- and a deterministic rank so the list can be worked top-down.
--
-- Grain: one row per currently actionable lead. Primary key: lead_id.
--
-- Actionable means the lead still needs an operational step at the reporting cutoff. That is either:
--   * an open (non-terminal) lead, or
--   * a terminal lead that still sits in an UNRESOLVED duplicate group, which an operator must still resolve.
-- Every other terminal lead is excluded: converted, closed-lost, withdrawn, invalid and already-merged leads do not
-- appear. In the current baseline the terminal exception matches no lead, because every duplicate group containing a
-- terminal lead has already been resolved; the rule is kept so the queue stays correct if that changes.
--
-- Source: int_acq__lead_spine, which already holds the latest valid lead state, the latest priority decision and
-- the latest applicable operational routing decision at one row per lead.
--
-- Action rank is fully deterministic: escalation first, then SLA urgency, then priority tier, then priority score,
-- then lead age, then the stable lead identifier. Two runs produce the same order.
--
-- Limitations: entirely synthetic. The recommended action follows declared hypothetical rules awaiting business
-- approval; it is a prompt for a human operator, not an instruction, and no action here is automated.
with params as (
    select max(case when parameter = 'reporting_cutoff_date' then cast(value as date) end) as cutoff_date
    from {{ ref('acquisition_reporting_parameters') }}
),

thresholds as (
    select max(case when parameter = 'capacity_warning_ratio'    then cast(value as double) end) as warning_ratio,
           max(case when parameter = 'capacity_hard_limit_ratio' then cast(value as double) end) as hard_limit
    from {{ ref('routing_evaluation_thresholds') }}
),

-- Duplicate groups that are still open. A terminal lead only remains actionable when it sits in one of these,
-- because the operator must still resolve the group. A MERGED lead whose group is already resolved needs nothing.
unresolved_duplicate_groups as (
    select distinct duplicate_group_id
    from {{ ref('int_crm__duplicate_resolution') }}
    where resolved_at is null
),

candidates as (
    select s.*
    from {{ ref('int_acq__lead_spine') }} s
    left join unresolved_duplicate_groups u on u.duplicate_group_id = s.duplicate_group_id
    where s.is_active
       or (s.is_terminal and u.duplicate_group_id is not null)
),

classified as (
    select
        c.*,
        th.warning_ratio,
        th.hard_limit,

        -- SLA status, from the first-contact clock
        case when c.first_contact_sla_breached and c.first_contact_sla_open then 'breached and open'
             when c.first_contact_sla_breached                              then 'breached'
             when c.first_contact_sla_open and c.hours_to_sla_target is not null
                  and c.hours_to_sla_target <= 24                           then 'due within 24 hours'
             when c.first_contact_sla_open                                  then 'running'
             when c.first_contact_sla_eligible                              then 'met'
             else 'no first-contact clock' end                              as sla_status,

        -- capacity status of the responsible RM, where there is one
        case when c.current_owner_rm_id is null                             then 'no current owner'
             when c.owner_utilization_ratio is null                         then 'no capacity evidence'
             when c.owner_utilization_ratio >= th.hard_limit                then 'owner at or above hard limit'
             when c.owner_utilization_ratio >= th.warning_ratio             then 'owner at or above warning'
             else 'owner has headroom' end                                  as capacity_status
    from candidates c
    cross join thresholds th
),

actioned as (
    select
        c.*,
        -- The recommended next action, in strict precedence. The first matching condition wins, so every lead gets
        -- exactly one action and the reason is the one that must actually be dealt with.
        case
            when c.current_lead_status = 'DUPLICATE_REVIEW' or c.queue_code = 'Q-DUP'
                then 'Resolve the duplicate match and merge into the surviving lead'
            when c.current_lead_status = 'VALIDATION_PENDING' or c.queue_code = 'Q-DRM'
                then 'Correct the missing or invalid lead data so the lead can be routed'
            when c.queue_code = 'Q-OOF'
                then 'Confirm the business operates outside the footprint and close with a disposition'
            when c.queue_code = 'Q-TER'
                then 'Resolve the territory so the lead can be assigned'
            when c.queue_code = 'Q-NSP'
                then 'Find specialist coverage or approve a generalist exception'
            when c.queue_code = 'Q-LNG'
                then 'Arrange service in the language the prospect stated'
            when c.queue_code = 'Q-CAP'
                then 'Release capacity or approve an exception so the lead can be assigned'
            when c.queue_code is not null
                then coalesce(c.queue_next_action, 'Review the held lead and decide the next step')
            when c.current_owner_rm_id is null and c.recommended_rm_id is not null
                then 'Assign to the recommended relationship manager'
            when c.current_owner_rm_id is null
                then 'Assign an owner'
            when c.first_contact_sla_breached and c.first_contact_sla_open
                then 'Make first contact now; the response promise is already missed'
            when c.first_contact_sla_open and c.hours_to_sla_target is not null and c.hours_to_sla_target <= 24
                then 'Make first contact today; the response promise is due within 24 hours'
            when not c.is_contacted
                then 'Make first contact'
            when c.is_contacted and not c.is_qualified and c.is_ageing
                then 'Progress or disposition the lead; it has been open beyond the ageing threshold'
            when c.is_contacted and not c.is_qualified
                then 'Continue qualification'
            else 'Progress the qualified lead toward an opportunity'
        end                                                                 as recommended_next_action,

        -- who owns that action
        case
            when c.queue_code is not null                                   then coalesce(c.queue_owner_role, 'Regional Sales Manager')
            when c.current_owner_rm_id is null                              then 'Regional Sales Manager'
            else 'Owning relationship manager'
        end                                                                 as responsible_role,

        -- escalation: a missed promise, an escalation clock, or a held lead that is also ageing
        (c.first_contact_sla_breached
         or c.open_breached_clocks > 0
         or (c.queue_code is not null and c.is_ageing))                     as requires_escalation
    from classified c
)

select
    a.lead_id,
    a.prospect_id,
    a.captured_at,
    a.days_since_capture                                                    as lead_age_days,

    -- current state
    a.current_lead_status,
    a.is_terminal,
    a.is_active,

    -- market and segmentation context
    a.cma_key,
    a.cma_code_sgc2021,
    a.cma_label_sgc2021,
    a.channel_code,
    a.channel_name,
    a.sme_segment,
    a.industry_grouping,
    a.industry_label,
    a.territory_id,

    -- priority
    a.priority_score,
    a.priority_tier,
    a.priority_tier_name,
    a.priority_eligibility,

    -- ownership and routing
    a.current_owner_rm_id,
    a.current_owner_queue_code,
    a.recommended_rm_id,
    a.routing_status,
    a.queue_code,
    a.queue_name,
    a.queue_reason,
    a.specialist_requirement_level,
    a.territory_eligibility_result,
    a.specialist_eligibility_result,
    a.language_eligibility_result,

    -- service health
    a.sla_status,
    a.first_contact_sla_id,
    a.first_contact_sla_target_at,
    a.hours_to_sla_target                                                   as hours_remaining_or_overdue,
    a.first_contact_sla_breached,
    a.open_breached_clocks,

    -- data quality and capacity
    a.is_duplicate_flagged                                                  as is_duplicate,
    a.duplicate_group_id,
    a.capacity_status,
    a.owner_utilization_ratio,

    -- the action
    a.recommended_next_action,
    a.responsible_role,
    a.requires_escalation,

    -- deterministic ordering: escalation, then SLA urgency, then priority, then age, then a stable identifier
    row_number() over (
        order by
            case when a.requires_escalation then 0 else 1 end,
            case a.sla_status when 'breached and open' then 0 when 'breached' then 1
                              when 'due within 24 hours' then 2 when 'running' then 3 else 4 end,
            case a.priority_tier when 'P1' then 1 when 'P2' then 2 when 'P3' then 3 else 9 end,
            coalesce(a.priority_score, -1) desc,
            a.days_since_capture desc,
            a.lead_id
    )                                                                       as action_rank,

    a.reporting_cutoff_date
from actioned a
