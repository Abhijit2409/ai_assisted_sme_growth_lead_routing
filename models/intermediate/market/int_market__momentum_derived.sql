-- Grain: one row per CMA x size member x industry member x month x derived measure (4 measures).
-- net_formation = openings(t) - closures(t); rates divide by the same series' active enterprises at t-1.
-- A derived value exists only when every input is available; it inherits an E flag from any input.
{%- set rates = [
    ('opening_rate', 'Opening rate', 'openings(t) / active(t-1)', 'opn', 'opn_ok', 'opn_e'),
    ('closure_rate', 'Closure rate', 'closures(t) / active(t-1)', 'cls', 'cls_ok', 'cls_e'),
    ('net_formation_rate', 'Net formation rate', '(openings(t) - closures(t)) / active(t-1)', 'opn - cls', '(opn_ok and cls_ok)', '(opn_e or cls_e)'),
] %}
with inputs as (
    select * from {{ ref('int_market__momentum_series_inputs') }}
)
select cma_code_sgc2016, ref_month, size_member_id, industry_member_id,
       'net_formation' as measure, 'Net formation (openings - closures)' as measure_label, 'openings(t) - closures(t)' as formula,
       case when opn_ok and cls_ok then opn - cls end as value,
       case when not (opn_ok and cls_ok) then 'unavailable_input' when opn_e or cls_e then 'derived_E' else 'derived' end as value_state
from inputs
{%- for measure, label, formula, num, num_ok, num_e in rates %}
union all
select cma_code_sgc2016, ref_month, size_member_id, industry_member_id,
       '{{ measure }}', '{{ label }}', '{{ formula }}',
       case when {{ num_ok }} and pact_ok then ({{ num }}) / pact end,
       case when not has_pact then 'no_prior_month'
            when not ({{ num_ok }} and pact_ok) then 'unavailable_input'
            when {{ num_e }} or pact_e then 'derived_E' else 'derived' end
from inputs
{%- endfor %}
