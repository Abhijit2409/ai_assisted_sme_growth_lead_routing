# Power BI build guide

Step-by-step instructions to build the **AI-Assisted SME Growth & Lead Routing** report in Power BI Desktop.

Written for someone who can find their way around Power BI Desktop but wants to be told exactly what to click. Follow
the sections in order — several early steps prevent faults that are painful to diagnose later.

> Synthetic CRM data, hypothetical rules, fictional bank. Nothing in this report is evidence about a real Canadian
> bank, and no visual should ever be captioned as if it were.

### Files this guide depends on

| File | What it gives you |
|---|---|
| `data_manifest.csv` | the 8 source files, their table names, grains, keys and expected row counts |
| `data_model_guide.md` | data types, relationships, disconnected tables, double-counting risks |
| `measures.dax` | all **83** DAX measures, each with its required format and QA target |
| `measure_catalog.csv` | measure to KPI to page mapping |
| `sme_acquisition_theme.json` | the report theme |
| `visual_specification.csv` | **the authoritative visual contract** — all 61 visuals |
| `wireframes/*.png` | the five page layouts |
| `dashboard_qa_checklist.md` | the reconciliation and review gate |

Throughout, `visual_specification.csv` is the source of truth. Where this guide and that file ever disagree, the
file wins.

---

# Part 1 — Build the model (steps 1–13)

## Step 1. Create a blank report

Open **Power BI Desktop**. On the start screen choose **Blank report**. If a sign-in prompt appears you can dismiss
it; nothing here needs a Power BI service account.

## Step 2. Save with the project title

**File → Save as** → navigate to `dashboard/power_bi/` in the repository → file name:

```
SME Growth & Acquisition Intelligence.pbix
```

Save again (**Ctrl+S**) after every numbered step below. Power BI does not auto-save.

## Step 3. Create the `DataRoot` parameter

This keeps the developer's absolute path out of every query.

1. **Home → Transform data** (opens Power Query Editor).
2. **Home → Manage Parameters → New Parameter.**
3. Name: `DataRoot` · Type: **Text** · Required: ticked · Current Value:

```
D:\AI assisted SME Growth & Lead Routing\data\analysis\acquisition_intelligence
```

4. **OK**, then **Home → Close & Apply.**

**To move the project to another machine later:** Transform data → Manage Parameters → change Current Value →
Close & Apply → Home → Refresh. Nothing else changes.

## Step 4. Import the eight CSV files, in order

Import in the order in `data_manifest.csv`. The order matters: `MarketFocus` must exist before `FunnelMonthly` so
the CMA key type is already correct when you build the relationship.

For **each** file:

1. **Home → Get data → Text/CSV.**
2. Browse to the file under the data-root folder, **Open**.
3. In the preview window click **Transform Data** (*not* Load — you must set types first).
4. In Power Query, click the **gear icon** beside **Source** in Applied Steps, and replace the hard-coded path with:

```
= Csv.Document(File.Contents(DataRoot & "\market_focus.csv"),[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])
```

   Substitute the matching file name each time. (Easier alternative: **Home → Advanced Editor** and edit the first
   line.) This is what makes the parameter actually take effect.
5. **Home → Use First Row as Headers** if Power BI has not done it already.

Order: `market_focus.csv`, `routing_load_by_rm.csv`, `funnel_monthly.csv`, `rm_performance_monthly.csv`,
`action_queue.csv`, `kpi_snapshot.csv`, `kpi_definitions.csv`, `routing_concentration.csv`.

## Step 5. Rename each query

In the Queries pane, right-click each query → **Rename**, using exactly the `power_bi_table_name` column of
`data_manifest.csv`:

`MarketFocus` · `RoutingLoadByRM` · `FunnelMonthly` · `RMPerformanceMonthly` · `ActionQueue` · `KPISnapshot` ·
`KPIDefinitions` · `RoutingConcentration`

Measure names and the visual specification both assume these exact names. A typo here breaks every DAX expression.

## Step 6. Fix the CMA key before loading — read this one carefully

**This is the single most likely thing to go wrong.**

All 41 values in `MarketFocus[cma_key]` look numeric (`001`, `205`, `602`…), so Power BI infers **Whole Number** and
turns `001` into `1`. `FunnelMonthly[cma_key]` contains the text `UNRESOLVED`, so it stays **Text**. The two columns
then never match. The relationship still *looks* perfectly healthy in the model diagram, and CMA 001 silently shows
nothing.

In Power Query, for **`MarketFocus`**, **`FunnelMonthly`** and **`ActionQueue`**:

1. Click the `cma_key` column header.
2. **Transform → Data Type → Text.**
3. If prompted, choose **Replace current** (not "Add new step").

Do the same for `cma_code_sgc2021`, `industry_grouping`, `province_code`, `home_cma_code_sgc2021`,
`primary_specialization_industry_code`, `duplicate_group_id` and `rm_id` wherever they appear.

**Verify immediately:** in the Power Query preview, `MarketFocus[cma_key]` must still show `001`, not `1`.

## Step 7. Assign the remaining data types

Apply the full type list in **`data_model_guide.md` section 4**. In short:

- all `reporting_month` and `reporting_cutoff_date` columns → **Date**
- `ActionQueue[captured_at]`, `ActionQueue[first_contact_sla_target_at]` → **Date/Time**
- all `*_rate`, `*_ratio`, `*_score`, `*_points`, `pillar_*`, `median_hours_*` → **Decimal Number**
- all `*_leads`, `*_count`, `*_assignments`, `*_days`, `*_rank` → **Whole Number**
- `is_*`, `at_or_above_*`, `no_capacity_evidence`, `requires_escalation`, `missing_*` → **True/False**
- every identifier, code, label, name, status and reason → **Text**

**Leave blanks blank.** Six CMAs have no `opportunity_score` and sixteen actionable leads have no `priority_tier`.
Do not replace either with `0` — a missing score is missing evidence, not a weak market.

## Step 8. Remove the provenance columns

Every file carries the same five audit columns repeated on every row: `source_models`, `period`, `statistical_unit`,
`evidence_eligibility`, `limitations`.

Keep them on **`KPIDefinitions` only**. On the other seven tables: select all five (Ctrl+click) → right-click →
**Remove Columns**. This shrinks the model and keeps the field list readable.

Then **Home → Close & Apply** and wait for the load to finish.

## Step 9. Create and mark the date table

**Modeling → New table**, and paste the `DimDate` expression from **`data_model_guide.md` section 6**.

Then:
1. Select `DimDate` → **Table tools → Mark as date table** → date column `Date` → OK.
2. Select `DimDate[Month Name]` → **Column tools → Sort by column → `Month Sort`**.

Without the sort-by step, month slicers and axes sort alphabetically (Apr, Aug, Dec…), which looks broken.

## Step 10. Create exactly five relationships

**Modeling → Manage relationships → New**, five times. Each is **Many-to-one (\*:1)** created from the fact side, or
equivalently **1 → \*** from the dimension side, with **Single** cross-filter direction.

| # | One side | Many side |
|---|---|---|
| 1 | `MarketFocus[cma_key]` | `FunnelMonthly[cma_key]` |
| 2 | `MarketFocus[cma_key]` | `ActionQueue[cma_key]` |
| 3 | `RoutingLoadByRM[rm_id]` | `RMPerformanceMonthly[rm_id]` |
| 4 | `DimDate[Month Start]` | `FunnelMonthly[reporting_month]` |
| 5 | `DimDate[Month Start]` | `RMPerformanceMonthly[reporting_month]` |

**Do not create** a relationship from `ActionQueue[recommended_rm_id]` or `[current_owner_rm_id]`. A lead has both a
current owner and a recommended RM; an active relationship on either makes RM-sliced lead counts mean something
different from their label. If you need one later, add it **inactive** and invoke it with `USERELATIONSHIP`.

**Do not set any relationship to Both.** Bi-directional filtering is unnecessary here and creates ambiguity.

### Verify the model now, before building anything

Drop a table visual on the canvas with `MarketFocus[cma_label_sgc2021]` and `SUM(FunnelMonthly[captured_leads])`.

- **Halifax must show 292.** If it does, the key type and relationship are right.
- A **blank** CMA row showing **2,112** is correct — those are leads whose geography never resolved. They keep the
  funnel reconciling to all 17,400 leads.
- If every CMA shows blank, step 6 did not take. Go back and fix the `cma_key` type.

Delete the test visual.

## Step 11. Leave three tables disconnected

`KPISnapshot`, `RoutingConcentration` and `KPIDefinitions` must have **no relationships at all**. They will sit as
islands in the model diagram — that is the intended shape.

- `KPISnapshot` is one pre-aggregated row for the whole book. Relating it would push its filter context onto the
  detail tables and silently change totals. It is for reconciliation only; never bind a visual to it.
- `RoutingConcentration` is one pre-aggregated row holding the validated top-decile share and workload Gini.
- `KPIDefinitions` is 24 rows of reference text with no key in common with any fact.

## Step 12. Import the theme

**View → Themes → Browse for themes** → select `dashboard/power_bi/sme_acquisition_theme.json` → **Open.**

You should see the page background turn light neutral and visual titles turn navy. The palette is navy `#1F3A5F`,
muted blue `#2E6F9E`, teal `#1A8F7A`, amber `#C8821A`, red `#9B2C24` on a light neutral `#F4F6F8` background. Red is
reserved for breaches and urgent exceptions — never use it decoratively.

## Step 13. Create the `_Measures` table and add the 83 measures

1. **Home → Enter data.** Leave the single empty column. Name the table `_Measures`. **Load.**
2. In the Data pane, expand `_Measures`, right-click `Column1` → **Delete**. (Power BI keeps the now-empty table —
   this is the standard way to hold measures.)
3. Select the `_Measures` table, then **Home → New measure** for each measure.

Open `measures.dax` beside Power BI and work through it **top to bottom**. The file is ordered deliberately: later
measures reference earlier ones, so building out of order produces temporary "cannot find name" errors.

For each measure:
- Copy the expression (everything from `Name =` down to the blank line before the next `//` comment). Skip the `//`
  comment lines — they are documentation, not DAX.
- Paste into the formula bar and press Enter.
- With the measure still selected, go to **Measure tools** and set the format exactly as the `// FORMAT:` comment
  above it states. For example "Percentage, 2 decimals" means Format = Percentage and Decimal places = 2.

Groups, in order: context (2) · funnel volumes (11) · maturity cohorts (6) · funnel rates (8) · SLA (5) ·
market (12) · action queue (10) · capacity (15) · routing concentration (9) · QA reconciliation (5). **83 total.**

**Format matters more than it looks.** A rate left as a decimal shows `0.995` where the reader expects `99.50%`, and
a count without a thousands separator shows `17400`. `measure_catalog.csv` lists the required format for all 83 in
one place.

**Do not hardcode any headline number into a visual.** Every figure on every page must come from a measure.

---

# Part 2 — Build the pages (steps 14–18)

## General mechanics, used on every page

**Create a page:** right-click the page tab bar → **New page** → double-click the tab → rename.

**Set the canvas:** with nothing selected, **Visualizations → Format page → Canvas settings → Type: 16:9**
(1280 × 720). Every coordinate in `visual_specification.csv` assumes this canvas.

**Add a visual:** click the visual type in the Visualizations pane → it appears on the canvas → drag fields from the
Data pane into the wells listed in the specification.

**Position a visual precisely:** select it → **Format visual → General → Properties → Position**, then type the X,
Y, Width and Height from the `position_x_y_w_h_px` column (format: `x,y,w,h`). Do this for every visual rather than
dragging by eye — it is faster and the pages line up.

**Add a title:** Format visual → General → Title → on → type the title from the specification.

**Sort:** click the visual's **⋯ (More options) → Sort axis**, choose the field or measure and the direction given in
the specification.

**Conditional formatting:** Format visual → **Cell elements** (tables) or the relevant colour property (cards and
charts) → **fx** → choose Rules → set the thresholds given in the specification.

**Edit interactions:** select a slicer or visual → **Format ribbon → Edit interactions** → then on each other visual
choose filter, highlight or none. Defaults are usually right; the exceptions are called out per page.

The wireframe PNG for each page shows the finished layout. Keep it open while you work.

---

## Page 1 — Executive Overview

**Business question:** where should management focus attention right now?

**Build order:** banner → KPI card row → funnel → supporting charts → warnings. Build the cards first; they are
quick, and seeing the headline numbers appear tells you immediately whether the model is sound.

**Visual IDs: EX-01 … EX-14.**

| Build | ID | What to add |
|---|---|---|
| 1 | **EX-01** | Text box banner. Type the report title; add a second line bound to `[Data Basis Note]` so the synthetic basis is stated on the page itself. Navy background, white text. |
| 2 | **EX-02** | Card → `[High-Opportunity CMA Count]` |
| 3 | **EX-03** | Card → `[Active Backlog]` |
| 4 | **EX-04** | Card → `[P1 Leads Requiring Action]`, amber value above 200 |
| 5 | **EX-05** | Card → `[Assignment Rate]` |
| 6 | **EX-06** | Card → `[Contact Rate]` |
| 7 | **EX-07** | Card → `[Qualification Rate (Mature)]` — the title must carry the word *mature* |
| 8 | **EX-08** | Card → `[Mature-Cohort Conversion Rate]` — title must carry *mature cohort* |
| 9 | **EX-09** | Card → `[SLA Breach Rate]`, red value above 10% |
| 10 | **EX-10** | Funnel visual → add the six measures in order: `[Captured Leads]`, `[Valid Leads]`, `[Assigned Leads]`, `[Contacted Leads]`, `[Qualified Leads (Mature)]`, `[Converted Leads (Mature)]` |
| 11 | **EX-11** | Clustered bar chart → Y `MarketFocus[cma_label_sgc2021]`, X `[Market Leads]`, filter `is_high_opportunity` is True, sort descending |
| 12 | **EX-12** | Clustered bar chart → Y `ActionQueue[recommended_next_action]`, X `[Actionable Leads]`, Top 6 by value, sort descending |
| 13 | **EX-13** | Multi-row card → `[RMs Above Capacity Warning]`, `[RMs At or Above Hard Limit]`, `[Routing Top-Decile Share]`, `[RMs With No Recommendations]` |
| 14 | **EX-14** | Text box with an amber left border, carrying the standing routing-concentration warning and pointing to page 5 |

**Filters and sorting:** EX-11 is filtered to high-opportunity markets only; EX-12 is Top 6. Nothing else is
filtered — this page is the unfiltered headline.

**Conditional formatting:** EX-04 amber above 200; EX-09 red above 10%; EX-13 amber category labels.

**Interactions:** this page is a summary, not an exploration surface. Select EX-10 and EX-11 in turn and set
**Edit interactions → None** against the KPI cards, so clicking a funnel stage or a market does not silently change
the headline numbers a reader is quoting.

**Interpretation notes.** The funnel's last two stages are mature-cohort only and must be labelled as such. The gap
between a 90% contact rate and a 12.5% conversion rate is the story of this page: the funnel loses its volume after
the first conversation, not at intake or routing.

**Common errors.** Binding a card to `KPISnapshot` instead of a measure — the card then ignores every slicer.
Dividing `[Converted Leads]` by `[Valid Leads]` to make a conversion rate; that mixes cohorts and is not valid.
Leaving rates unformatted so they read `0.9949789745`.

---

## Page 2 — Market Opportunity

**Business question:** where should the bank focus SME acquisition effort?

**Build order:** banner → slicers → evidence-warning cards → map and scatter → ranked table.

**Visual IDs: MO-01 … MO-10.**

| Build | ID | What to add |
|---|---|---|
| 1 | **MO-01** | Text box banner. Subtitle must state that the opportunity score is a **current planning snapshot, never applied to history**. |
| 2 | **MO-02** | Slicer → `MarketFocus[province_code]`, dropdown |
| 3 | **MO-03** | Slicer → `MarketFocus[opportunity_classification]`, dropdown. Leave blank as a selectable item so unscored markets stay reachable. |
| 4 | **MO-04** | Slicer → `MarketFocus[evidence_tier]`, tile style, horizontal |
| 5 | **MO-05** | Card → `[CMAs Without Opportunity Score]`, amber. Subtitle: *reported as missing evidence, never as zero opportunity.* |
| 6 | **MO-06** | Card → `[Leads Outside CMA Scope]`, amber |
| 7 | **MO-07** | Map → Location `MarketFocus[cma_label_sgc2021]`, bubble size `[Market Leads]`, colour `[Average Opportunity Score]` |
| 8 | **MO-08** | Scatter chart → X `[Average Opportunity Score]`, Y `[Market Leads]` (log scale), size `[Market Active Backlog]`, details `cma_label_sgc2021` |
| 9 | **MO-09** | Table → the ranked market detail; columns and measures per the specification |
| 10 | **MO-10** | Card → `[High-Opportunity Markets Below Median Activity]`, amber, placed to the right of the table |

**About the map (MO-07).** Power BI geocodes from text, and Canadian CMA labels are not always resolved reliably.
Build it, then check every bubble lands in the right province. **If geocoding is wrong or incomplete, replace MO-07
with a clustered bar chart** of `[Average Opportunity Score]` by `cma_label_sgc2021` and record the substitution in
`dashboard_qa_checklist.md` row H4. A map that places Canadian cities incorrectly is worse than no map.

**Filters and sorting:** MO-07 and MO-08 filter to `opportunity_score is not blank`, because an unscored market has
no position on either. MO-09 sorts by `opportunity_rank` ascending with blanks last.

**Conditional formatting:** data bars on `[Market Leads]` in MO-09. Blank `opportunity_rank` must display as a dash,
never as `0`.

**Interactions:** leave cross-filtering on between MO-07, MO-08 and MO-09 — selecting a market in one and seeing it
in the others is the main way this page is used.

**Interpretation notes.** Nine CMAs carry the strongest external signal. The scatter answers the real question:
*are we actually present where the signal is strong?* MO-05 and MO-06 exist so a gap in evidence is never silently
read as a weak market.

**Common errors.** Replacing blank scores with 0, which drags six markets to the bottom of every ranking and
misrepresents missing evidence as a finding. Grouping this page by a fact-table CMA column instead of `MarketFocus`
— the page is explicitly about scored markets.

---

## Page 3 — Acquisition Funnel

**Business question:** where are leads being delayed or lost?

**Build order:** banner → slicers → maturity card → funnel and trend → breakdowns → delay table.

**Visual IDs: AF-01 … AF-11.**

| Build | ID | What to add |
|---|---|---|
| 1 | **AF-01** | Text box banner. Subtitle must state the cohort basis is **capture month** and that conversion uses the **90-day mature cohort**. |
| 2 | **AF-02** | Slicer → `DimDate[Month Name]` |
| 3 | **AF-03** | Slicer → `FunnelMonthly[channel_name]` |
| 4 | **AF-04** | Slicer → `FunnelMonthly[sme_segment]`, tile horizontal |
| 5 | **AF-05** | Slicer → `FunnelMonthly[industry_label]` |
| 6 | **AF-06** | Card → `[Share of Valid Leads Immature]`, grey. Subtitle: *excluded from the conversion rate, not counted as failures.* |
| 7 | **AF-07** | Funnel visual, same six measures as EX-10 |
| 8 | **AF-08** | Line and clustered column → X `DimDate[Month Name]`, column `[Captured Leads]`, lines `[Assignment Rate]`, `[Contact Rate]`, `[SLA Compliance Rate]`, `[Mature-Cohort Conversion Rate]` on a secondary 0–1 axis |
| 9 | **AF-09** | Clustered bar → Y `channel_name`, X `[Mature-Cohort Conversion Rate]` and `[Contact Rate]` |
| 10 | **AF-10** | Clustered bar → Y `sme_segment` then `industry_label` as a drill-down hierarchy, X `[Mature-Cohort Conversion Rate]` |
| 11 | **AF-11** | Table → by CMA: `[Captured Leads]`, `[Ageing Leads]`, `[Typical Hours to Assignment]`, `[Typical Hours to First Contact]`, `[SLA Compliance Rate]`, `[Reassignment Rate]` |

**Maturity labelling is mandatory.** AF-07, AF-08, AF-09 and AF-10 all show conversion. Each title or subtitle must
contain "mature cohort". AF-06 sits beside them stating how much of the book is excluded. Without that label a
reader will compare this conversion rate against an all-cohort figure from elsewhere and reach a wrong conclusion.

**Filters and sorting:** AF-08 sorts by `Month Sort` ascending (not alphabetically). AF-09 and AF-10 sort by
conversion descending.

**Conditional formatting:** red background on `[SLA Compliance Rate]` below 80% in AF-11.

**Interactions:** AF-02 to AF-05 filter every visual on the page. Leave AF-07 set to **None** against the slicers if
you want the full funnel always visible as a reference; otherwise leave defaults.

**Use the fact table's own CMA label in AF-11**, not `MarketFocus`, so leads with unresolved geography remain
visible on this page rather than disappearing.

**Common errors.** Averaging the pre-computed `*_rate` columns instead of using the measures — that weights a
3-lead cell the same as an 800-lead cell. Forgetting the `Sort by column` step in step 9, so months run
alphabetically.

---

## Page 4 — Lead Action Queue

**Business question:** which prospects need action today, and what exactly is the action?

**Build order:** banner → slicers → KPI cards → the detail table last (it is the biggest and benefits from having
the filters already in place).

**Visual IDs: LQ-01 … LQ-13.**

| Build | ID | What to add |
|---|---|---|
| 1 | **LQ-01** | Text box banner. Subtitle: *one row per currently actionable lead, ranked deterministically.* |
| 2 | **LQ-02** | Slicer → `ActionQueue[priority_tier]`, tile horizontal. Keep blank selectable — 16 leads have a suppressed tier. |
| 3 | **LQ-03** | Slicer → `ActionQueue[sla_status]`, tile horizontal, red tiles for the two breached states |
| 4 | **LQ-04** | Slicer → `routing_status` then `queue_name` as a hierarchy |
| 5 | **LQ-05** | Slicer → `ActionQueue[capacity_status]` |
| 6 | **LQ-06** | Slicer → `ActionQueue[requires_escalation]`, two tiles |
| 7 | **LQ-07** | Card → `[Actionable Leads]` |
| 8 | **LQ-08** | Card → `[Overdue P1 Leads]`, red |
| 9 | **LQ-09** | Card → `[Unassigned Leads]`, amber |
| 10 | **LQ-10** | Card → `[Queued Leads]`, amber |
| 11 | **LQ-11** | Card → `[Leads Requiring Escalation]`, red |
| 12 | **LQ-12** | Card → `[Average Lead Age (Days)]` |
| 13 | **LQ-13** | Table → the full actionable lead detail, 18 columns, sorted by `action_rank` ascending |

**LQ-13 column order** is given in the specification and matters: identify the lead, place it, judge its priority,
see who owns it and what routing decided, then the SLA position, then the action and who owns that action.

**Sorting:** `action_rank` ascending. That rank is the declared deterministic work order — escalation, then SLA
urgency, then priority tier, then score, then age, then a stable identifier. Do not re-sort by another column as the
default; users can click a header themselves.

**Conditional formatting on LQ-13:**
- `hours_remaining_or_overdue` — red background below 0 (overdue), 1 decimal place.
- `sla_status` — red for `breached` and `breached and open`, amber for `due within 24 hours`.
- `requires_escalation` — red dot icon where True.
- Word wrap **on for `recommended_next_action` only**; leave it off elsewhere or rows become very tall.

**Interactions:** all six slicers filter LQ-13 and all six cards. Verify with the combinations in section F of the QA
checklist — for example tier `P1` must leave 217 rows and escalation True must leave 213.

**Deliberately absent:** no legal or operating business name appears on this page. The action workflow does not need
it, and synthetic names on an operational list invite being mistaken for real customer records.

**Common errors.** Adding `prospect_id` as a visible column (it identifies nothing a user acts on). Sorting by
`priority_score` instead of `action_rank`, which quietly discards the SLA-urgency and escalation precedence.

---

## Page 5 — RM Capacity and Routing

**Business question:** where is execution constrained by capacity, and how evenly is recommended work spread?

**Build order:** banner → the standing interpretation band → slicers → capacity cards → distribution charts →
RM table → concentration visuals.

**Visual IDs: RC-01 … RC-13.**

| Build | ID | What to add |
|---|---|---|
| 1 | **RC-01** | Text box banner. Subtitle: *capacity is a declared model, not a measured workload.* |
| 2 | **RC-02** | Text box, full width, amber band — see below |
| 3 | **RC-03** | Slicer → `DimDate[Month Name]` |
| 4 | **RC-04** | Slicer → `RMPerformanceMonthly[home_sales_region]` |
| 5 | **RC-05** | Slicer → `RMPerformanceMonthly[rm_segment]`, tile horizontal |
| 6 | **RC-06** | Card → `[RMs Above Capacity Warning]`, amber |
| 7 | **RC-07** | Card → `[Operational Hard-Cap Breaches]`, teal when 0, red above 0 |
| 8 | **RC-08** | Card → `[Average Utilization]` |
| 9 | **RC-09** | Column chart → `utilization_ratio` binned to 0.05, with amber reference line at 0.80 and red at 1.00, both labelled |
| 10 | **RC-10** | Stacked column → X `DimDate[Month Name]`, Y `[RM P1 Assignments]`, `[RM P2 Assignments]`, `[RM P3 Assignments]` |
| 11 | **RC-11** | Table → RM detail for the latest month, sorted by `[Average Utilization]` descending |
| 12 | **RC-12** | Clustered bar → `rm_id` by `[Total Operational Recommendations]`, sorted descending, no data labels (140 bars) |
| 13 | **RC-13** | Multi-row card → `[Routing Top-Decile Share]`, `[Routing Workload Gini]`, `[Max Recommendations to One RM]`, `[RMs With No Recommendations]` |

**RC-02 is not optional.** It is a full-width amber band carrying this sentence, in navy 12pt semibold:

> **Hard-capacity compliance does not guarantee balanced workload distribution.**

It must remain visible without scrolling and must not be removed, collapsed or moved below the fold. The report
shows zero hard-cap breaches alongside a top-decile share above 70%; without this sentence a reader will take the
zero as evidence that workload is balanced. It is not. The routing preference rules reward the relationship manager
with the most headroom, and in a lightly loaded year that is repeatedly the same person.

**Conditional formatting on RC-11:** red background on `[Average Utilization]` at or above 1.00, amber from 0.80;
teal data bars on `[Remaining Capacity Points]`.

**Interactions:** RC-12 and RC-13 read whole-period totals from `RoutingLoadByRM` and `RoutingConcentration`. The
Gini in RC-13 comes from the validated published value and **will not respond to the month slicer** — that is
correct, and the card subtitle should say "whole period" so nobody reads it as a filtered figure.

**Interpretation notes.** Most of the network has headroom; a small number of RMs are over the line. The
concentration result is the honest finding of this page and is reported rather than smoothed. The routing rules are
deliberately unchanged — adjusting preference weights after seeing a concentration statistic would be exactly the
outcome-driven tuning this project avoids.

**Common errors.** Trying to recompute the Gini in DAX with `RANKX`: it returns **0.7709** instead of the validated
**0.7729**, because `RANKX` gives tied RMs the same rank while the validated calculation breaks ties on a stable
identifier. Use `[Routing Workload Gini]` as supplied. Also: summing `[RM Assignments]` across months and calling it
"leads" — a reassigned lead is counted in each month it was assigned, so that total exceeds the distinct lead count.

---

# Part 3 — Finish (steps 19–20)

## Step 19. Reconcile the dashboard

Work through **`dashboard_qa_checklist.md`** in full. It covers refresh and row counts (section A), data types
including the CMA key trap (B), keys and relationships (C), **22 headline KPI targets** (D), the maturity treatment
(E), **13 filter behaviour tests** (F), and a 15-point visual quality review (G).

The fastest way through section D: build a temporary table visual with no slicers applied, drop in the measures, and
check each against its target. `[QA Reconciliation Status]` returns **PASS** only when the assignment rate,
conversion rate, SLA breach rate and actionable lead count all match the validated snapshot. Delete that visual once
every row passes.

If a value does not match, **the dashboard is wrong — do not adjust the target.** The targets come from the
independently validated acquisition-intelligence layer.

## Step 20. Save the final PBIX

**Ctrl+S.** Close Power BI Desktop, reopen the file, and confirm it loads with all five pages rendering and no
visual showing an error. Then capture a full-page screenshot of each page into `outputs/dashboard/` and review them
against section G of the QA checklist.

---

# Build Completion Checklist

The detailed gate is **`dashboard_qa_checklist.md`** — work through every row there. The report is complete only
when all of the following minimum conditions hold:

- [ ] The PBIX opens cleanly after a restart, with no refresh or load error.
- [ ] All **8** tables are present with the expected row counts: MarketFocus 41, FunnelMonthly 10,028,
      RMPerformanceMonthly 1,620, ActionQueue 1,408, KPISnapshot 1, KPIDefinitions 24, RoutingLoadByRM 140,
      RoutingConcentration 1.
- [ ] `cma_key` is **Text** in all three tables, and CMA `001` still reads `001`.
- [ ] Exactly **5** relationships exist, all single-direction; `KPISnapshot`, `RoutingConcentration` and
      `KPIDefinitions` are disconnected.
- [ ] The Halifax spot-check returns **292**, and the blank CMA row returns **2,112**.
- [ ] All **83** measures exist in `_Measures`, each with its required format applied.
- [ ] All **5** pages exist, named as in this guide.
- [ ] All **61** visuals from `visual_specification.csv` are built and positioned to their declared coordinates.
- [ ] Every section-D KPI target reconciles, and `[QA Reconciliation Status]` returns **PASS**.
- [ ] Every conversion visual is labelled as mature cohort.
- [ ] The RC-02 routing-concentration sentence is visible on page 5 without scrolling.
- [ ] Blank opportunity scores display blank or as a dash, never `0`.
- [ ] No page requires horizontal scrolling; no clipped labels; no overlapping visuals.
- [ ] No real bank name, logo or brand colour appears anywhere.
- [ ] The synthetic-data basis is stated on every page.
- [ ] Five full-page screenshots are captured in `outputs/dashboard/` and reviewed.

Stop at that point. The dashboard is the deliverable; recommendations, a roadmap and presentation material are
separate pieces of work.
