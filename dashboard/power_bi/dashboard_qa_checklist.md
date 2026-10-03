# Dashboard QA checklist

Work through this after building the report and before sharing it. Record Pass or Fail and a note for every row.
Every target below comes from the validated acquisition-intelligence layer. If a value does not match, the dashboard
is wrong — do not adjust the target.

> Synthetic CRM data, hypothetical rules, fictional bank. Nothing here is evidence about a real Canadian bank.

---

## A. Data refresh and source integrity

| # | Check | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| A1 | **Home → Refresh** completes with no error | All 8 queries refresh | | |
| A2 | `DataRoot` parameter drives every query | No absolute path appears in any query's M code except the parameter default | | |
| A3 | `MarketFocus` row count | 41 | | |
| A4 | `FunnelMonthly` row count | 10,028 | | |
| A5 | `RMPerformanceMonthly` row count | 1,620 | | |
| A6 | `ActionQueue` row count | 1,408 | | |
| A7 | `KPISnapshot` row count | 1 | | |
| A8 | `KPIDefinitions` row count | 24 | | |
| A9 | `RoutingLoadByRM` row count | 140 | | |
| A10 | `RoutingConcentration` row count | 1 | | |

## B. Data types — the one that silently breaks the model

| # | Check | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| B1 | `MarketFocus[cma_key]` data type | **Text** (not Whole Number) | | |
| B2 | `FunnelMonthly[cma_key]` and `ActionQueue[cma_key]` | Text | | |
| B3 | CMA `001` still reads `001`, not `1` | Table visual on `MarketFocus[cma_key]` shows `001` | | |
| B4 | `FunnelMonthly[reporting_month]` and `RMPerformanceMonthly[reporting_month]` | Date | | |
| B5 | `ActionQueue[captured_at]`, `[first_contact_sla_target_at]` | Date/Time | | |
| B6 | Boolean columns are True/False, not text | `is_high_opportunity`, `requires_escalation`, `at_or_above_capacity_warning` | | |
| B7 | `DimDate` marked as a date table | Table tools shows the date-table badge | | |

## C. Keys and relationships

| # | Check | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| C1 | `MarketFocus[cma_key]` unique | 41 distinct of 41 rows | | |
| C2 | `ActionQueue[lead_id]` unique | 1,408 distinct of 1,408 rows | | |
| C3 | `ActionQueue[action_rank]` unique and 1..1,408 | min 1, max 1,408, no gaps | | |
| C4 | `RoutingLoadByRM[rm_id]` unique | 140 distinct | | |
| C5 | Exactly 5 relationships exist | See data_model_guide section 7 | | |
| C6 | Every relationship is **single** direction | No bi-directional arrows in the model view | | |
| C7 | `KPISnapshot`, `RoutingConcentration`, `KPIDefinitions` are **disconnected** | Three islands in the model diagram | | |
| C8 | No relationship on `recommended_rm_id` or `current_owner_rm_id` | None present, or present and **inactive** | | |
| C9 | Relationship integrity spot-check | `MarketFocus[cma_label_sgc2021]` = Halifax with `[Captured Leads]` shows **292** | | |
| C10 | Unresolved geography behaves as designed | A blank CMA row shows **2,112** captured leads | | |

## D. Headline KPI reconciliation

Build a temporary table visual with no slicers applied and confirm each measure. Targets come from
`kpi_snapshot.csv` and `routing_concentration.csv`.

| # | Measure | Target | Pass/Fail | Notes |
|---|---|---|---|---|
| D1 | `[High-Opportunity CMA Count]` | **9** | | |
| D2 | `[Captured Leads]` | **17,400** | | |
| D3 | `[Active Backlog]` | **1,408** | | |
| D4 | `[Actionable Leads]` | **1,408** | | |
| D5 | `[P1 Leads Requiring Action]` | **217** | | |
| D6 | `[Leads Requiring Escalation]` | **213** | | |
| D7 | `[Assignment Rate]` | **99.50%** | | |
| D8 | `[Contact Rate]` | **90.07%** | | |
| D9 | `[Qualification Rate (Mature)]` | **27.82%** | | |
| D10 | `[Mature-Cohort Conversion Rate]` | **12.52%** | | |
| D11 | `[Duplicate Rate]` | **5.38%** | | |
| D12 | `[SLA Breach Rate]` | **13.15%** | | |
| D13 | `[SLA Breaches]` | **2,079** | | |
| D14 | `[RMs Above Capacity Warning]` | **5** | | |
| D15 | `[RMs At or Above Hard Limit]` | **4** | | |
| D16 | `[Operational Hard-Cap Breaches]` | **0** | | |
| D17 | `[Routing Top-Decile Share]` | **72.31%** | | |
| D18 | `[Routing Workload Gini]` | **0.773** | | |
| D19 | `[Max Recommendations to One RM]` | **3,216** | | |
| D20 | `[RMs With No Recommendations]` | **8** | | |
| D21 | `[Total Operational Recommendations]` | **17,950** | | |
| D22 | `[QA Reconciliation Status]` | **PASS** | | |

Delete the temporary visual once every row passes.

## E. Maturity treatment

| # | Check | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| E1 | `[Valid Leads (Mature)]` | 12,064 | | |
| E2 | `[Converted Leads (Mature)]` | 1,511 | | |
| E3 | `[Valid Leads (Immature)]` | 3,869 | | |
| E4 | Mature + immature = `[Valid Leads]` | 12,064 + 3,869 = 15,933 | | |
| E5 | `[Share of Valid Leads Immature]` | 24.3% | | |
| E6 | Every conversion visual is labelled | Title or subtitle contains "mature cohort" | | |
| E7 | No visual divides `[Converted Leads]` by `[Valid Leads]` | All-cohort conversion is never shown as a rate | | |

## F. Filter and interaction behaviour

Test each selection, then clear it before the next.

| # | Selection | Expected behaviour | Pass/Fail | Notes |
|---|---|---|---|---|
| F1 | Acquisition Funnel → channel `CH-PTR` | `[Mature-Cohort Conversion Rate]` ≈ **16.88%**, `[Captured Leads]` = **1,291** | | |
| F2 | Acquisition Funnel → channel `CH-DIG` | `[Captured Leads]` = **8,954** | | |
| F3 | Acquisition Funnel → segment `micro` | Rates recalculate; no visual errors | | |
| F4 | Acquisition Funnel → a single month | `[Captured Leads]` between 1,328 and 1,582 | | |
| F5 | Lead Action Queue → tier `P1` | Row count = **217** | | |
| F6 | Lead Action Queue → SLA status `breached` + `breached and open` | Row count = **206** | | |
| F7 | Lead Action Queue → escalation = True | Row count = **213** | | |
| F8 | Lead Action Queue → routing status `queued` | Row count = **19** | | |
| F9 | Market Opportunity → classification `Stronger external opportunity signal` | 9 CMAs remain | | |
| F10 | Market Opportunity → evidence tier `3` | Unscored markets remain visible with blank score, not 0 | | |
| F11 | RM page → a single sales region | Capacity cards recalculate without error | | |
| F12 | Clearing every slicer restores the D-section targets | All headline values return | | |
| F13 | Cross-highlighting does not blank an unrelated visual | No "can't display" placeholders | | |

## G. Visual quality review

Review each page at 100% zoom on a 16:9 canvas (1280×720).

| # | Check | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| G1 | All five pages exist and are named | Executive Overview, Market Opportunity, Acquisition Funnel, Lead Action Queue, RM Capacity and Routing | | |
| G2 | No visual shows an error or "can't display this visual" | Clean on every page | | |
| G3 | No blank visual caused by a broken field reference | Clean | | |
| G4 | No horizontal scrolling required on any page | Fits 1280 px | | |
| G5 | No clipped or truncated axis or category labels | Readable | | |
| G6 | No overlapping visuals | Clean spacing | | |
| G7 | Percentages show 1 or 2 decimals, never 6 | Consistent | | |
| G8 | Counts use a thousands separator | 17,400 not 17400 | | |
| G9 | Theme applied | Navy, muted blue, teal, amber, red on light neutral | | |
| G10 | Red is used only for breaches and urgent exceptions | Not decorative | | |
| G11 | No real bank name, logo or brand colour appears | Clean | | |
| G12 | The synthetic-data basis is stated on every page | `[Data Basis Note]` or equivalent visible | | |
| G13 | The routing concentration statement is visible without scrolling on the RM page | "Hard-capacity compliance does not guarantee balanced workload distribution." | | |
| G14 | No legal or operating business name appears on the action queue | By design | | |
| G15 | Blank opportunity scores show as blank or a dash, never 0 | 6 CMAs | | |

## H. Export and sign-off

| # | Check | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| H1 | Report saved as `.pbix` under `dashboard/power_bi/` | File opens cleanly after a restart | | |
| H2 | Full-page screenshot captured for each of the 5 pages | Saved under `outputs/dashboard/` | | |
| H3 | Screenshots reviewed against section G | Issues corrected and re-captured | | |
| H4 | If the CMA map could not geocode reliably, the substitution is recorded | Noted here and in the documentation | | |
| H5 | Working tree contains only intended dashboard changes | `git status` reviewed | | |

---

## Known values that are correct and should not be "fixed"

- **2,112 leads sit outside CMA scope.** Their geography never resolved. They appear under a blank CMA when slicing
  by a `MarketFocus` column and are counted in the funnel total of 17,400. This is intended.
- **6 CMAs have no opportunity score.** They cannot be scored on the available evidence. Blank is the correct
  display; zero would misrepresent a gap in evidence as a weak market.
- **16 actionable leads have a blank priority tier.** Their priority was suppressed by a declared override rule.
- **`MarketFocus[lead_count]` totals 15,288, not 17,400.** It covers CMA-resolved leads only.
- **8 RMs received no routing recommendation.** That is the concentration finding, not missing data.
- **`[Operational Hard-Cap Breaches]` is 0.** That is the designed outcome of the capacity-aware routing policy.
