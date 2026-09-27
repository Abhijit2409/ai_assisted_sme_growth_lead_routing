-- Reviewed rules E7: categories that are never summed as mutually exclusive groups.
select * from {{ ref('non_additive_category_rules') }}
