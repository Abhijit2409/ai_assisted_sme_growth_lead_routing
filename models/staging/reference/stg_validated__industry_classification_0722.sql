-- 0722 industry members with class and parent (from the table metadata).
select cast(industry_id as integer) as industry_member_id, industry as industry_label, industry_class, parent_industry_id
from {{ source('validated_profiles', 'industry_classification_0722') }}
