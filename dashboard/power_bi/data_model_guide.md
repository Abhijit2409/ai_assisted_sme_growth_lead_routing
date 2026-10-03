# Data model guide

How to load the eight validated CSV files into Power BI Desktop and wire them into a model that produces correct
totals. Follow the order below — several steps prevent silent errors that are hard to find later.

> All CRM data is synthetic. The model presents a hypothetical framework for a fictional bank. Nothing in it is
> evidence about a real Canadian bank.

---

## 1. The data-root parameter

Create this **before** importing anything, so no absolute path is baked into a query.

1. **Home → Transform data** to open Power Query Editor.
2. **Home → Manage Parameters → New Parameter.**
3. Name `DataRoot`, Type **Text**, Required, Current Value:

```
D:\AI assisted SME Growth & Lead Routing\data\analysis\acquisition_intelligence
```

Every query then builds its path as `DataRoot & "\file.csv"`. To move the project to another machine, change this one
value (**Home → Transform data → Manage Parameters**, edit Current Value, **Close & Apply**).

---

## 2. Import order

Import in this order. It is not arbitrary — the dimension-like tables come first so the relationship dialog defaults
are sensible, and `MarketFocus` must exist before `FunnelMonthly` so the CMA key type is already correct when you
create the relationship.

| # | File | Table name |
|---|---|---|
| 1 | `market_focus.csv` | `MarketFocus` |
| 2 | `routing_load_by_rm.csv` | `RoutingLoadByRM` |
| 3 | `funnel_monthly.csv` | `FunnelMonthly` |
| 4 | `rm_performance_monthly.csv` | `RMPerformanceMonthly` |
| 5 | `action_queue.csv` | `ActionQueue` |
| 6 | `kpi_snapshot.csv` | `KPISnapshot` |
| 7 | `kpi_definitions.csv` | `KPIDefinitions` |
| 8 | `routing_concentration.csv` | `RoutingConcentration` |

---

## 3. The one mistake that will break the model

**`cma_key` must be Text in every table that has it.**

All 41 values in `MarketFocus[cma_key]` are numeric-looking (`001`, `205`, `602`…). Power BI's type detection infers
**Whole Number**, which turns `001` into `1`. `FunnelMonthly[cma_key]` contains the value `UNRESOLVED`, so it stays
Text. The two columns then never match, and the relationship silently returns nothing for CMA 001 while looking
perfectly healthy in the diagram.

In Power Query, for `MarketFocus`, `FunnelMonthly` and `ActionQueue`: click the `cma_key` column header, then
**Transform → Data Type → Text**. If Power BI offers "Replace current conversion", choose **Replace current**.

Also set `cma_code_sgc2021`, `industry_grouping`, `province_code`, `home_cma_code_sgc2021`,
`primary_specialization_industry_code` and `duplicate_group_id` to **Text** for the same reason.

---

## 4. Column data types

Apply these after import. Anything not listed can keep its detected type.

### MarketFocus (41 rows)
| Column | Type |
|---|---|
| `cma_key`, `cma_label_sgc2021`, `province_code`, `opportunity_classification`, `dominant_pillar` | Text |
| `opportunity_score`, `pillar_scale`, `pillar_momentum`, `pillar_dynamism`, `pillar_commercial_fit`, `pillar_stability`, `lead_share_of_total`, `mature_cohort_conversion_rate`, `available_capacity_points` | Decimal Number |
| `opportunity_rank`, `evidence_tier`, `prospect_count`, `lead_count`, `active_lead_backlog`, `qualified_leads`, `converted_leads`, `mature_valid_leads`, `mature_converted_leads`, `p1_leads`, `p2_leads`, `p3_leads`, `unassigned_active_leads`, `queued_active_leads`, `open_sla_breaches`, `first_contact_sla_breaches`, `ageing_leads`, `rms_home_based_here`, `rms_at_or_above_warning`, `data_quality_exceptions` | Whole Number |
| `is_high_opportunity`, `missing_opportunity_score`, `missing_acquisition_activity` | True/False |

`is_high_opportunity`, `opportunity_score`, `opportunity_rank`, `opportunity_classification` and `evidence_tier` are
**blank for 6 CMAs** that cannot be scored. Leave them blank. Do not replace them with 0 — a missing score is not a
weak market, and the Market Opportunity page must show it as missing evidence.

### FunnelMonthly (10,028 rows)
| Column | Type |
|---|---|
| `reporting_month`, `reporting_cutoff_date` | Date |
| `cma_key`, `cma_code_sgc2021`, `cma_label_sgc2021`, `channel_code`, `channel_name`, `sme_segment`, `industry_grouping`, `industry_label` | Text |
| All `*_leads`, `*_mature`, `*_immature`, `first_contact_sla_*` counts | Whole Number |
| `assignment_rate`, `contact_rate`, `qualification_rate`, `mature_cohort_conversion_rate`, `duplicate_rate`, `first_contact_sla_compliance_rate`, `reassignment_rate`, `median_hours_to_assignment`, `median_hours_to_first_contact` | Decimal Number |

The pre-computed `*_rate` columns are **row-level rates for one funnel cell**. Never average them — use the DAX
measures, which re-divide the summed numerator by the summed denominator.

### RMPerformanceMonthly (1,620 rows)
| Column | Type |
|---|---|
| `reporting_month`, `reporting_cutoff_date` | Date |
| `rm_id`, `rm_display_name`, `rm_code`, `rm_segment`, `rm_role`, `home_territory_id`, `home_territory_code`, `home_sales_region`, `home_cma_code_sgc2021`, `primary_specialization_industry_code`, `capacity_state`, `availability_status` | Text |
| `utilization_ratio`, `first_contact_sla_compliance_rate`, `max_simulated_utilization`, `base_capacity_points`, `capacity_limit_points`, `weighted_workload_points`, `remaining_capacity_points`, `min_remaining_capacity_points` | Decimal Number |
| `is_specialist`, `at_or_above_capacity_warning`, `at_or_above_hard_limit`, `no_capacity_evidence` | True/False |
| All lead and recommendation counts | Whole Number |

### ActionQueue (1,408 rows)
| Column | Type |
|---|---|
| `captured_at`, `first_contact_sla_target_at` | Date/Time |
| `reporting_cutoff_date` | Date |
| `lead_id`, `prospect_id`, `cma_key`, `cma_code_sgc2021`, `cma_label_sgc2021`, `channel_code`, `channel_name`, `sme_segment`, `industry_grouping`, `industry_label`, `territory_id`, `current_lead_status`, `priority_tier`, `priority_tier_name`, `priority_eligibility`, `current_owner_rm_id`, `current_owner_queue_code`, `recommended_rm_id`, `routing_status`, `queue_code`, `queue_name`, `queue_reason`, `specialist_requirement_level`, `sla_status`, `first_contact_sla_id`, `capacity_status`, `recommended_next_action`, `responsible_role`, `duplicate_group_id` | Text |
| `priority_score`, `hours_remaining_or_overdue`, `owner_utilization_ratio` | Decimal Number |
| `lead_age_days`, `open_breached_clocks`, `action_rank` | Whole Number |
| `is_terminal`, `is_active`, `first_contact_sla_breached`, `is_duplicate`, `requires_escalation` | True/False |

`priority_tier` is blank for 16 leads whose priority was suppressed by a declared override. Leave blank; do not
relabel as P3.

### Remaining four tables
`KPISnapshot`, `KPIDefinitions`, `RoutingLoadByRM`, `RoutingConcentration` — accept detected types, but set
`rm_id` to **Text**.

---

## 5. Remove the provenance columns

Every file carries five identical text columns: `source_models`, `period`, `statistical_unit`,
`evidence_eligibility`, `limitations`. They are audit provenance, repeated on every row.

Keep them on **`KPIDefinitions` only** (useful in the definition drill-through). In Power Query, remove them from the
other seven tables: select the five columns → right-click → **Remove Columns**. This cuts the model size sharply and
stops them appearing in field lists.

---

## 6. Create the date table

Power BI needs a real date dimension for the month slicer to behave. In **Modeling → New table**:

```
DimDate =
VAR MinDate = DATE ( 2025, 6, 1 )
VAR MaxDate = DATE ( 2026, 5, 31 )
RETURN
ADDCOLUMNS (
    CALENDAR ( MinDate, MaxDate ),
    "Year",        YEAR ( [Date] ),
    "Month Start", DATE ( YEAR ( [Date] ), MONTH ( [Date] ), 1 ),
    "Month Name",  FORMAT ( [Date], "MMM yyyy" ),
    "Month Sort",  YEAR ( [Date] ) * 100 + MONTH ( [Date] )
)
```

Then: select `DimDate` → **Table tools → Mark as date table** → Date column `Date`.
Select `Month Name` → **Column tools → Sort by column → Month Sort**.

The fact tables hold the **first day of the month**, so relate on `Month Start`.

---

## 7. Relationships

Create exactly these five. Every one is **single direction** and **one-to-many**, from the one side to the many side.

| # | From (one) | To (many) | Cardinality | Filter direction | Active |
|---|---|---|---|---|---|
| 1 | `MarketFocus[cma_key]` | `FunnelMonthly[cma_key]` | 1 → * | Single | Yes |
| 2 | `MarketFocus[cma_key]` | `ActionQueue[cma_key]` | 1 → * | Single | Yes |
| 3 | `RoutingLoadByRM[rm_id]` | `RMPerformanceMonthly[rm_id]` | 1 → * | Single | Yes |
| 4 | `DimDate[Month Start]` | `FunnelMonthly[reporting_month]` | 1 → * | Single | Yes |
| 5 | `DimDate[Month Start]` | `RMPerformanceMonthly[reporting_month]` | 1 → * | Single | Yes |

### Do not create these
- **No relationship from `ActionQueue[recommended_rm_id]` or `[current_owner_rm_id]` to `RoutingLoadByRM`.** A lead has
  both a current owner and a recommended RM; an active relationship on either one would make every RM-sliced lead
  count mean something different from what the label says. If you later need it, add it as an **inactive**
  relationship and call it explicitly with `USERELATIONSHIP`.
- **No relationship to `KPISnapshot`, `RoutingConcentration` or `KPIDefinitions`.** See below.
- **No bi-directional filtering anywhere.** It is not needed here and it creates ambiguity once two fact tables share
  a dimension.

### Expected unmatched rows — this is correct, not a defect
`FunnelMonthly` and `ActionQueue` both contain `cma_key = "UNRESOLVED"` for leads whose geography never resolved to a
CMA. `MarketFocus` has no such row, so those fact rows have no match and appear under a blank CMA when you slice by a
`MarketFocus` column.

That is intended: the funnel must still reconcile to all 17,400 leads. Two consequences to respect:

- On the **Market Opportunity** page, group by `MarketFocus` columns — unresolved leads are correctly excluded there,
  and the page shows the unresolved count separately as a data-quality warning.
- On the **Acquisition Funnel** and **Lead Action Queue** pages, group by `FunnelMonthly[cma_label_sgc2021]` or
  `ActionQueue[cma_label_sgc2021]` (the fact table's own column), so unresolved leads stay visible.

---

## 8. Tables that must remain disconnected

| Table | Why it must stay disconnected |
|---|---|
| `KPISnapshot` | One pre-aggregated row for the whole book. Relating it would push its filter context onto the detail tables and silently change totals. Use it only to reconcile — never bind a visual to it. |
| `RoutingConcentration` | One pre-aggregated row holding the validated top-decile share and workload Gini. Read with `SELECTEDVALUE`; relating it has no meaning. |
| `KPIDefinitions` | Reference text, 24 rows, no key shared with any fact. Use it in a definitions table or drill-through page. |

Power BI will show these as islands in the model diagram. That is the intended shape.

---

## 9. Double-counting risks

Read this before building any visual that combines tables.

1. **Never add `FunnelMonthly` lead counts to `ActionQueue` lead counts.** The funnel counts every lead ever captured
   (17,400, by capture cohort); the action queue counts only the 1,408 still needing work, today. They answer
   different questions and their sum is meaningless.
2. **Never add `MarketFocus[lead_count]` to `FunnelMonthly[captured_leads]`.** Both count the same leads, at
   different scopes. `MarketFocus[lead_count]` totals **15,288** - CMA-resolved leads only, because the file
   holds one row per CMA and carries no unresolved bucket. `FunnelMonthly[captured_leads]` totals **17,400** -
   every lead, including the 2,112 whose geography never resolved. Adding them double counts, and quoting
   either as "total leads" without naming the scope is misleading. Use the funnel total for any all-lead
   figure, and expect the Market Opportunity page to show 15,288.
3. **Never average the pre-computed `*_rate` columns.** Averaging per-cell rates weights a cell of 3 leads the same as
   a cell of 800. Always use the DAX measures, which sum numerator and denominator first.
4. **`RMPerformanceMonthly[assigned_leads]` is attributed to the month of assignment.** A reassigned lead is counted in
   each month it was assigned, so summing across all months exceeds the distinct lead count. This is correct for an
   activity measure; just never call that total "leads".
5. **Do not mix mature and all-cohort conversion in one visual.** `converted_leads` (all cohorts) and
   `converted_leads_mature` have different denominators. Only the mature pair is a valid rate.
6. **`RoutingLoadByRM` and `RMPerformanceMonthly` both count recommendations.** `RoutingLoadByRM` is the whole-period
   total per RM (17,950 across 140 RMs); `RMPerformanceMonthly[operational_recommendations]` is the monthly split.
   Use one or the other in a visual, never both.
7. **`MarketFocus` is both dimension and fact.** It supplies CMA attributes *and* its own measures. When you slice a
   `FunnelMonthly` measure by a `MarketFocus` column the filter flows correctly (1 → *), but the reverse does not —
   which is exactly why the relationship is single-direction.

---

## 10. Field visibility

Hide these from report view (right-click → **Hide in report view**) so the field list stays readable:
all `reporting_cutoff_date` columns, `FunnelMonthly[cma_key]`, `ActionQueue[cma_key]`, `FunnelMonthly[cma_code_sgc2021]`,
`ActionQueue[prospect_id]`, `DimDate[Month Sort]`, and every raw `*_rate` column on `FunnelMonthly`
(the measures replace them).

---

## 11. Verify the model before building visuals

In a blank table visual, drop in these three and confirm:

| Check | Expected |
|---|---|
| `COUNTROWS(MarketFocus)` | 41 |
| `COUNTROWS(FunnelMonthly)` | 10,028 |
| `COUNTROWS(ActionQueue)` | 1,408 |
| `COUNTROWS(RMPerformanceMonthly)` | 1,620 |
| `COUNTROWS(RoutingLoadByRM)` | 140 |
| `SUM(FunnelMonthly[captured_leads])` | 17,400 |
| `SUM(RoutingLoadByRM[operational_recommendations])` | 17,950 |

Then put `MarketFocus[cma_label_sgc2021]` on rows with `SUM(FunnelMonthly[captured_leads])`. If **Halifax shows 292**
and a blank row shows the unresolved leads, the key type and relationship are right. If every CMA shows blank, the
`cma_key` text conversion in section 3 did not take.
