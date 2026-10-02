-- One row per duplicate group: the flagged lead, the earlier matched lead, the rule and the resolution (synthetic data).
select
    g.duplicate_group_id,
    g.match_rule_id,
    g.match_tier,
    f.lead_id                                                                          as flagged_lead_id,
    g.matched_lead_id,
    g.surviving_lead_id,
    g.review_decision,
    case
        when f.current_lead_status = 'MERGED' and g.match_tier = 'review' then 'merged_after_review'
        when f.current_lead_status = 'MERGED' then 'merged_automatically'
        when g.review_decision = 'rejected' then 'kept_separate_after_review'
        else 'awaiting_review'
    end                                                                                as resolution,
    g.flagged_at,
    g.resolved_at,
    date_diff('second', g.flagged_at, g.resolved_at) / 3600.0                          as hours_to_resolution
from {{ ref('stg_crm__duplicate_group') }} g
join {{ ref('stg_crm__lead') }} f on f.duplicate_group_id = g.duplicate_group_id
