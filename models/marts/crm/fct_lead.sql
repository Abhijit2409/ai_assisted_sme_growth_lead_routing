-- One row per lead with current status, milestones and market context (fictional contact fields excluded). Synthetic data (fictional bank, businesses and staff); event period 2025-06-01 to 2026-05-31 UTC; no score, ranking or recommendation.
select
    l.lead_id,
    l.prospect_id,
    l.captured_at,
    l.first_channel_code,
    l.postal_fsa,
    l.province_code,
    l.cma_code_sgc2021,
    l.territory_id,
    l.geography_status,
    l.local_time_zone,
    l.market_evidence_flag,
    m.market_scenario_id,
    m.market_opportunity_score,
    m.market_opportunity_rank,
    m.market_opportunity_classification,
    l.stated_product_interest,
    l.stated_service_language,
    l.consent_to_contact_flag,
    l.priority_band,
    s.current_lead_status,
    s.is_terminal,
    s.disposition_code,
    s.status_changed_at,
    s.first_ready_at,
    s.first_assigned_at,
    s.first_contacted_at,
    s.qualified_at,
    s.converted_at,
    s.audited_reopening_count,
    l.duplicate_group_id,
    l.surviving_lead_id,
    a.rm_id                                                                                    as current_owner_rm_id,
    a.queue_code                                                                               as current_owner_queue_code
from {{ ref('stg_crm__lead') }} l
join {{ ref('int_crm__lead_current_status') }} s using (lead_id)
join {{ ref('int_crm__lead_market_context') }} m using (lead_id)
left join {{ ref('int_crm__active_assignment') }} a using (lead_id)
