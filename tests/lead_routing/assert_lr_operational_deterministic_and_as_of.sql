-- The operational run is deterministic in the declared processing order, and uses nothing from after the decision.
with op as (select * from {{ ref('mart_routing_recommendation_operational') }})

-- processing_sequence must be a 1..n permutation
select 'processing_sequence is not a complete 1..n ordering' as failure, count(*)::varchar as detail
from (select count(*) n, count(distinct processing_sequence) d, min(processing_sequence) lo,
             max(processing_sequence) hi from op)
where lo <> 1 or hi <> n or d <> n
having count(*) > 0

-- and it must follow the declared order: timestamp, then tier, then score desc, then SLA pressure desc, then id
union all
select 'the processing order does not follow the declared tie-break sequence', count(*)::varchar
from op a join op b on b.processing_sequence = a.processing_sequence + 1
where not (a.as_of_timestamp < b.as_of_timestamp
        or (a.as_of_timestamp = b.as_of_timestamp and a.tiebreak_priority_tier_order < b.tiebreak_priority_tier_order)
        or (a.as_of_timestamp = b.as_of_timestamp and a.tiebreak_priority_tier_order = b.tiebreak_priority_tier_order
            and a.tiebreak_score_order < b.tiebreak_score_order)
        or (a.as_of_timestamp = b.as_of_timestamp and a.tiebreak_priority_tier_order = b.tiebreak_priority_tier_order
            and a.tiebreak_score_order = b.tiebreak_score_order and a.tiebreak_sla_order < b.tiebreak_sla_order)
        or (a.as_of_timestamp = b.as_of_timestamp and a.tiebreak_priority_tier_order = b.tiebreak_priority_tier_order
            and a.tiebreak_score_order = b.tiebreak_score_order and a.tiebreak_sla_order = b.tiebreak_sla_order
            and a.decision_id < b.decision_id))
having count(*) > 0

-- as-of safety: the capacity evidence may never post-date the decision
union all
select 'a capacity snapshot used by the operational recommendation post-dates its decision', count(*)::varchar
from op where capacity_snapshot_as_of_utc > as_of_timestamp
having count(*) > 0

-- no outcome or ground-truth field may appear in the operational lineage
union all
select 'the operational models publish an outcome, ground-truth or propensity column', count(*)::varchar
from information_schema.columns
where table_name in ('int_lead_routing__operational_assignment', 'mart_routing_recommendation_operational')
  and regexp_matches(column_name, 'conversion|converted|closed_lost|opportunity_id|propensity|truth|expected_|latent_')
having count(*) > 0

-- the only historical columns permitted are the comparison outputs on the mart, never on the simulation itself
union all
select 'the capacity-ledger simulation reads a historical routing outcome', count(*)::varchar
from information_schema.columns
where table_name = 'int_lead_routing__operational_assignment'
  and regexp_matches(column_name, 'historical|selected_rm')
having count(*) > 0
