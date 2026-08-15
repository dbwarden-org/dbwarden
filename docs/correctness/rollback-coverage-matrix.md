# Rollback Coverage Matrix

This matrix describes rollback behavior implemented by the PostgreSQL and ClickHouse handlers. It distinguishes real inverse SQL from conditional rollback, explicit irreversible changes, and placeholder or manual rollback comments.

## Classification

- `real`: Emits executable reverse SQL for the normal operation.
- `conditional`: Emits executable reverse SQL only when dbwarden can prove the operation is structurally reversible from static metadata and all required prior state is present. Conditional must be explicit and limited. If rollback safety is not secured, the operation must not be classified as conditional.
- `irreversible`: The operation is intentionally marked or treated as not safely reversible.
- `placeholder`: Rollback is a comment, manual instruction, or incomplete placeholder.
- `no-op`: No reverse action is expected for the operation.

## Warning Policy

- `irreversible` rollback emits a warning during standard migration generation.
- `conditional` rollback emits a warning only in verbose mode.
- `placeholder` rollback is a hard generation error by default.
- Placeholder rollback comments are allowed only for explicitly irreversible or manual cases, not as a successful rollback claim.
- The committed-migration escape hatch is an explicit annotation: `-- dbwarden: irreversible`.
- `--allow-placeholder-rollback` is intentionally not the primary escape hatch and should be treated only as a future local-development convenience.

## ClickHouse Recreate Policy

ClickHouse table recreation uses a detect-and-refuse rollback policy. Unknown means unsafe.

| Case | Classification | Behavior |
| --- | --- | --- |
| Structurally lossy transitions | irreversible | Engines such as `ReplacingMergeTree`, `SummingMergeTree`, `AggregatingMergeTree`, `CollapsingMergeTree`, and `VersionedCollapsingMergeTree` may collapse, deduplicate, aggregate, or otherwise lose row-level detail. Reverse recreate cannot restore data that no longer exists. |
| Structurally reversible transitions | conditional | Both engine families must be known row-preserving, such as `MergeTree`, `ReplicatedMergeTree`, `Memory`, `Log`, `TinyLog`, and `StripeLog`. Rollback emits reverse recreate SQL, and should be promoted only when covered by live round-trip tests. |
| Cannot-tell transitions | irreversible | Custom engines, unknown engines, unparsed engine behavior, or transitions whose safety depends on data are treated as irreversible. |

## PostgreSQL

| Operation | Classification | Notes |
| --- | --- | --- |
| `create_schema` | real | Rollback drops the schema. |
| `drop_schema` | real | Rollback recreates the schema. |
| `create_table` | real | Rollback drops the table. |
| `drop_table` | conditional | Recreates from captured `state_table`; strict generation fails if the prior table definition is unavailable. |
| `alter_table_comment` | real | Restores prior comment or `NULL`. |
| `add_column` | real | Rollback drops the column. |
| `drop_column` | conditional | Re-adds the column when definition exists; data is not restored. |
| `rename_column` | real | Renames back to the original column name. |
| `alter_column_type` | conditional | Emits inverse type change; safety depends on database castability and helper behavior. |
| `alter_column_nullable` | real | Restores previous nullability. |
| `alter_column_autoincrement` | real | Toggles sequence/default behavior back. |
| `alter_column_default` | real | Restores previous default from `rollback_attrs`. |
| `alter_column_comment` | real | Restores prior column comment or `NULL`. |
| `alter_pg_column_meta` | conditional | Depends on PostgreSQL column metadata helper support. |
| `alter_enum_add_value` | irreversible | PostgreSQL enum values cannot be removed directly. |
| `create_type` | real | Rollback drops enum type. |
| `drop_type` | real | Rollback recreates enum from saved values. |
| `create_domain` | real | Rollback drops domain. |
| `drop_domain` | conditional | Recreates domain when definition is present; strict generation fails if the prior definition is unavailable. |
| `create_composite_type` | real | Rollback drops composite type. |
| `drop_composite_type` | conditional | Recreates type when definition is present; strict generation fails if the prior definition is unavailable. |
| `create_function` | real | Rollback drops the created function. |
| `drop_function` | conditional | Recreates only when saved definition exists. |
| `create_event_trigger` | real | Rollback drops event trigger. |
| `drop_event_trigger` | conditional | Recreates only when full trigger info exists. |
| `create_extended_statistics` | real | Rollback drops statistics. |
| `drop_extended_statistics` | conditional | Recreates only when statistic info exists. |
| `add_index` | conditional | Diff carries inverse state; executable SQL depends on `_build_index_sql`. |
| `drop_index` | conditional | Recreates when index attributes are present. |
| `add_unique_constraint` | real | Rollback drops unique constraint. |
| `drop_unique_constraint` | real | Rollback recreates unique constraint. |
| `rename_unique_constraint` | real | Rollback renames back. |
| `add_check_constraint` | real | Rollback drops check constraint. |
| `drop_check_constraint` | real | Rollback recreates check constraint. |
| `add_foreign_key` | real | Rollback drops foreign key. |
| `drop_foreign_key` | real | Rollback recreates foreign key. |
| `add_grant` | real | Rollback revokes grant. |
| `revoke_grant` | real | Rollback grants privilege back. |
| `add_schema_grant` | real | Rollback revokes schema grant. |
| `revoke_schema_grant` | real | Rollback grants schema privilege back. |
| `alter_default_privileges` | conditional | Privilege grant/revoke is reversible when the previous privilege state is represented in the plan. |
| `create_role` | real | Rollback drops role. |
| `drop_role` | conditional | Recreates the role from attributes captured in `rollback_attrs` from the live schema or baseline snapshot. |
| `alter_role` | conditional | Restores prior role attributes captured in `rollback_attrs` from the live schema or baseline snapshot. |
| `create_sequence` | real | Rollback drops sequence. |
| `drop_sequence` | conditional | Recreates only when sequence info exists. |
| `alter_pg_rls` | real | Restores previous RLS/force setting. |
| `add_policy` | real | Rollback drops policy. |
| `drop_policy` | conditional | Recreates policy from `rollback_attrs` when available. |
| `alter_policy` | conditional | Restores prior policy from `rollback_attrs` when available. |
| `alter_pg_storage_param` | real | Restores previous table storage parameter or resets it. |
| `alter_pg_table` | conditional | Fillfactor and logged/unlogged are reversible; unsupported physical rewrites fail strict generation unless handled manually. |
| `add_exclude_constraint` | real | Rollback drops exclusion constraint. |
| `drop_exclude_constraint` | real | Rollback recreates exclusion constraint. |
| `alter_column_statistics` | real | Restores prior statistics target or resets to the PostgreSQL default target with `SET STATISTICS -1`. |
| `alter_pg_partition` | irreversible | Partition strategy changes require a table rebuild or a hand-authored migration. dbwarden refuses to claim automatic rollback. |
| `attach_partition` | real | Rollback detaches partition. |
| `detach_partition` | real | Rollback attaches partition with saved bound. |
| `rename_table` | conditional | Delegates to rename SQL helper. |
| `create_trigger` | conditional | Rollback drops trigger; create is placeholder if definition is missing. |
| `drop_trigger` | conditional | Recreates only when trigger definition exists. |
| `alter_trigger` | placeholder | Emit path is not implemented and uses comments. |
| `alter_view` | real | Drops and recreates view or materialized view with previous definition. |
| `refresh_matview` | no-op | Refresh has no stateful rollback. |

## ClickHouse

| Operation | Classification | Notes |
| --- | --- | --- |
| `alter_ch_options` | conditional | TTL/settings/order-by can emit inverse ALTERs; immutable/recreate-required keys can require manual handling. |
| `recreate_ch_table` | conditional or irreversible | Reverse recreate is emitted only when both engines are known row-preserving. Lossy or unknown engine transitions emit an irreversible rollback comment. |
| `alter_ch_column` | conditional | Emits inverse ALTERs for supported type/default/codec/TTL/materialized/alias/nullability/LowCardinality changes. |
| `modify_mv_query` | real | Restores previous materialized view SELECT. |
| `modify_mv_refresh` | conditional | Restores previous refresh when present; absent refresh is no-op, refresh removal is manual. |
| `alter_ch_dict` | conditional | Create/drop reversible when options exist; unsupported alter keys emit manual comments. |
| `create_ch_named_collection` | real | Rollback drops collection. |
| `drop_ch_named_collection` | conditional | Recreates the named collection from prior entries and overridable flags captured in `rollback_attrs`. |
| `alter_ch_named_collection` | conditional | Drops and recreates from full target/prior state so entry and overridable changes are reversible when prior state is captured. |
| `alter_ch_projection` | conditional | Add/drop/replace are reversible when prior definitions are in rollback attrs. |
| `alter_ch_skip_index` | conditional | Add/drop/replace are reversible when prior definitions are in rollback attrs. |
| `apply_data_op` | irreversible | Uses authored rollback when provided; otherwise remains explicitly irreversible because arbitrary data mutations cannot be inferred. |
| `create_ch_agg_target` | real | Rollback drops aggregate target table. |
| `drop_ch_agg_target` | conditional | Recreates from captured options; incomplete options can produce incomplete SQL. |
| `alter_ch_comment` | conditional | Restores table and column comments when prior values exist; otherwise may no-op. |
| `grant_ch_privilege` | real | Rollback revokes privilege. |
| `revoke_ch_privilege` | real | Rollback grants privilege back. |
| `create_ch_role` | real | Rollback drops role. |
| `drop_ch_role` | conditional | Recreates the role and captured settings from `rollback_attrs`. |
| `alter_ch_role` | conditional | Restores captured role settings with drop and recreate from `rollback_attrs`. |
| `create_ch_user` | real | Rollback drops user. |
| `drop_ch_user` | conditional | Recreates the user from captured auth, host, roles, default roles, and settings profile in `rollback_attrs`. |
| `alter_ch_user` | conditional | Restores captured user state with drop and recreate from `rollback_attrs`. |
| `create_ch_quota` | real | Rollback drops quota. |
| `drop_ch_quota` | conditional | Recreates quota from captured interval, limits, and role assignments in `rollback_attrs`. |
| `alter_ch_quota` | conditional | Restores captured quota state with drop and recreate from `rollback_attrs`. |
| `create_ch_row_policy` | real | Rollback drops row policy. |
| `drop_ch_row_policy` | conditional | Recreates the row policy from captured table, predicate, roles, and mode in `rollback_attrs`. |
| `alter_ch_row_policy` | conditional | Restores the row policy from prior state using drop and recreate from `rollback_attrs`. |
| `create_ch_settings_profile` | real | Rollback drops settings profile. |
| `drop_ch_settings_profile` | conditional | Recreates settings profile from captured settings and role assignments in `rollback_attrs`. |
| `alter_ch_settings_profile` | conditional | Restores prior settings and role assignments from `rollback_attrs`. |
| `ChRbacHandler` | no-op | No op types are emitted by this wrapper. |

## Manual and Irreversible Boundaries

These cases are not open rollback gaps. They are explicit policy boundaries where dbwarden either emits no rollback because no schema state changes, requires authored SQL, or marks the operation irreversible.

| Case | Policy |
| --- | --- |
| PostgreSQL partition strategy changes | Require a table rebuild or hand-authored migration. dbwarden refuses automatic rollback because changing partition strategy is not a metadata-only inverse. |
| PostgreSQL `REFRESH MATERIALIZED VIEW` | Rollback is intentionally no-op because refresh does not change schema definition. |
| PostgreSQL enum value additions | Irreversible in PostgreSQL because enum values cannot be removed directly. |
| ClickHouse data operations | Require authored rollback SQL when a reverse data operation exists; otherwise they remain explicitly irreversible. |
| ClickHouse engine transitions | Conditional reverse recreate is allowed only for known row-preserving engine families. Lossy and unknown transitions are irreversible by policy. |

For ordinary generated drop and alter operations, prior state is captured at generation time from the live schema snapshot or from the baseline snapshot used by offline and squash workflows. The captured definition is serialized through `rollback_attrs`, preserved in the migration plan, and consumed by the backend handler that emits rollback SQL. If the required prior state is missing, strict generation fails instead of accepting placeholder rollback.
