{{ config(tags=['crm_data']) }}
-- Duplicate groups have two valid members; only exact and high-confidence tiers merge automatically; the survivor is the earlier matched lead.
select g.duplicate_group_id from {{ ref('stg_crm__duplicate_group') }} g
left join {{ ref('stg_crm__lead') }} f on f.duplicate_group_id = g.duplicate_group_id
left join {{ ref('stg_crm__lead') }} m on m.lead_id = g.matched_lead_id
where f.lead_id is null or m.lead_id is null or g.member_count <> 2 or m.captured_at >= f.captured_at or m.current_lead_status = 'MERGED'
   or (f.current_lead_status = 'MERGED') <> (g.surviving_lead_id is not null and (g.match_tier <> 'review' or g.review_decision = 'confirmed'))
   or (f.current_lead_status = 'MERGED' and f.surviving_lead_id <> g.matched_lead_id)
   or (g.match_tier = 'review' and f.current_lead_status = 'MERGED' and not exists (select 1 from {{ ref('stg_crm__lead_status_history') }} h
       where h.lead_id = f.lead_id and h.from_status = 'DUPLICATE_REVIEW' and h.to_status = 'MERGED'))
