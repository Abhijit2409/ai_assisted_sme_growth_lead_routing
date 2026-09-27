{#- Passes when the model has exactly the expected number of rows (a declared, evidence-backed coverage). -#}
{% test row_count_equals(model, expected) %}
select count(*) as actual_rows from {{ model }} having count(*) <> {{ expected }}
{% endtest %}
