-- Where are leads being delayed or lost? The acquisition funnel by capture cohort, with an explicit numerator and
-- denominator beside every rate so each one can be recomputed and audited.
--
-- Grain: reporting month x CMA x acquisition channel x SME segment x industry grouping.
-- Primary key: (reporting_month, cma_key, channel_code, sme_segment, industry_grouping).
--
-- Source: int_acq__lead_spine only. Every history table has already been collapsed to one row per lead there, so no
-- count in this mart can be inflated by status, assignment, SLA or routing history.
--
-- Cohort basis: a lead belongs to the month it was CAPTURED, and every measure follows that lead wherever it went.
-- This answers "how did the demand we took in during March perform", which is the question the funnel exists to
-- answer. It is deliberately not an activity-in-month view.
--
-- Maturity: conversion is reported only on cohorts that have had the declared 90 days to convert, and
-- qualification only on cohorts that have had 30 days. Recently captured leads are excluded from those
-- denominators entirely rather than counted as failures; the immature counts are published separately so the
-- exclusion is visible.
--
-- The external opportunity score is NOT carried here. It is a current planning snapshot and applying it to a
-- historical month would misrepresent what was known at the time.
--
-- Limitations: entirely synthetic CRM data over hypothetical rules. Nothing here is evidence of real conversion
-- behaviour, and no rate is used to tune any priority or routing rule.
with spine as (
    select * from {{ ref('int_acq__lead_spine') }}
)

select
    capture_month                                                       as reporting_month,
    cma_key,
    cma_code_sgc2021,
    cma_label_sgc2021,
    channel_code,
    channel_name,
    sme_segment,
    industry_grouping,
    max(industry_label)                                                 as industry_label,

    -- ---------------------------------------------------------------- volumes (each a count of distinct leads)
    count(*)                                                            as captured_leads,
    count(*) filter (where is_valid_lead)                               as valid_leads,
    count(*) filter (where is_duplicate_lead)                           as duplicate_leads,
    count(*) filter (where is_assigned)                                 as assigned_leads,
    count(*) filter (where is_contacted)                                as contacted_leads,
    count(*) filter (where is_qualified)                                as qualified_leads,
    count(*) filter (where is_converted)                                as converted_leads,
    count(*) filter (where is_closed_or_disqualified)                   as closed_or_disqualified_leads,
    count(*) filter (where is_active)                                   as active_backlog,
    count(*) filter (where is_ageing)                                   as ageing_leads,
    count(*) filter (where is_reassigned)                               as reassigned_leads,

    -- ---------------------------------------------------------------- maturity-controlled cohorts
    count(*) filter (where is_conversion_mature and is_valid_lead)      as valid_leads_mature,
    count(*) filter (where is_conversion_mature and is_converted)       as converted_leads_mature,
    count(*) filter (where not is_conversion_mature and is_valid_lead)  as valid_leads_immature,
    count(*) filter (where is_qualification_mature and is_contacted)    as contacted_leads_mature,
    count(*) filter (where is_qualification_mature and is_qualified)    as qualified_leads_mature,

    -- ---------------------------------------------------------------- first-contact SLA
    count(*) filter (where first_contact_sla_eligible)                  as first_contact_sla_eligible,
    count(*) filter (where first_contact_sla_settled)                   as first_contact_sla_settled,
    count(*) filter (where first_contact_sla_settled and first_contact_sla_breached) as first_contact_sla_breached,
    count(*) filter (where first_contact_sla_settled and not first_contact_sla_breached) as first_contact_sla_met,

    -- ---------------------------------------------------------------- rates, each numerator / denominator above
    case when count(*) filter (where is_valid_lead) > 0
         then count(*) filter (where is_assigned) * 1.0
              / count(*) filter (where is_valid_lead) end              as assignment_rate,
    case when count(*) filter (where is_assigned) > 0
         then count(*) filter (where is_contacted) * 1.0
              / count(*) filter (where is_assigned) end                as contact_rate,
    case when count(*) filter (where is_qualification_mature and is_contacted) > 0
         then count(*) filter (where is_qualification_mature and is_qualified) * 1.0
              / count(*) filter (where is_qualification_mature and is_contacted) end as qualification_rate,
    case when count(*) filter (where is_conversion_mature and is_valid_lead) > 0
         then count(*) filter (where is_conversion_mature and is_converted) * 1.0
              / count(*) filter (where is_conversion_mature and is_valid_lead) end as mature_cohort_conversion_rate,
    case when count(*) > 0
         then count(*) filter (where is_duplicate_lead) * 1.0 / count(*) end as duplicate_rate,
    case when count(*) filter (where first_contact_sla_settled) > 0
         then count(*) filter (where first_contact_sla_settled and not first_contact_sla_breached) * 1.0
              / count(*) filter (where first_contact_sla_settled) end  as first_contact_sla_compliance_rate,
    case when count(*) filter (where is_assigned) > 0
         then count(*) filter (where is_reassigned) * 1.0
              / count(*) filter (where is_assigned) end                as reassignment_rate,

    -- ---------------------------------------------------------------- durations (medians; nulls excluded, never zero-filled)
    median(hours_to_assignment)                                         as median_hours_to_assignment,
    median(hours_to_first_contact)                                      as median_hours_to_first_contact,

    max(reporting_cutoff_date)                                          as reporting_cutoff_date
from spine
group by 1, 2, 3, 4, 5, 6, 7, 8
