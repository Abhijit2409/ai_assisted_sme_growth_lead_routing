{#- Passes when no combination of the key columns occurs more than once (one row per declared grain).
    NULL key parts are grouped as values, so a declared nullable key part stays inside the uniqueness check. -#}
{% test unique_key_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as occurrences
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
