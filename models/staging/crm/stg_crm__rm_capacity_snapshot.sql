-- One row per employed RM x business day (national business calendar) (synthetic; fictional). Types every column as in crm_field_dictionary.csv; no business logic.
select
    cast(capacity_snapshot_id as varchar)           as capacity_snapshot_id,
    cast(rm_id as varchar)                          as rm_id,
    cast(snapshot_date as date)                     as snapshot_date,
    cast(availability_status as varchar)            as availability_status,
    cast(available_capacity_points as decimal(6,2)) as available_capacity_points,
    cast(weighted_workload_points as decimal(6,2))  as weighted_workload_points,
    cast(utilization_ratio as decimal(6,4))         as utilization_ratio,
    cast(capacity_state as varchar)                 as capacity_state,
    cast(rule_version_id as varchar)                as rule_version_id,
    cast(snapshot_as_of_utc as timestamp)           as snapshot_as_of_utc,
    cast(specialist_support_points as decimal(6,2)) as specialist_support_points,
    cast(created_at as timestamp)                   as created_at,
    cast(updated_at as timestamp)                   as updated_at,
    cast(record_source as varchar)                  as record_source
from {{ source('synthetic_crm', 'crm_rm_capacity_snapshot') }}
