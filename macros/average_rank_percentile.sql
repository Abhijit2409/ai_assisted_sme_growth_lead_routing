{#- Percentile rank on a 0-100 scale with average ranks for ties:
    100 x (average rank - 1) / (n - 1), ascending by value within the partition. The lowest value scores 0, the highest 100,
    and tied values share the mean of the ranks they occupy (DuckDB's percent_rank would give ties the lowest rank). -#}
{% macro average_rank_percentile(value_expr, partition_by) -%}
    100.0 * (
        rank() over (partition by {{ partition_by }} order by {{ value_expr }})
        + (count(*) over (partition by {{ partition_by }}, {{ value_expr }}) - 1) / 2.0
        - 1
    ) / nullif(count(*) over (partition by {{ partition_by }}) - 1, 0)
{%- endmacro %}
