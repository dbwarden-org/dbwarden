from dbwarden.engine.core.statement_order import MigrationStatement


class DataHandler:
    object_type = "declarative_data"

    def emit(self, op, *, db_name=None):
        attrs = op.upgrade_attrs

        def render(steps):
            pieces = []
            for step in steps:
                for guard in step["guards"]:
                    pieces.append(
                        f"-- dbwarden data guard ({guard['timing']}): {guard['message']}\n-- {guard['query']}"
                    )
                pieces.extend(step["sql"])
            return "\n\n".join(pieces) or "-- No data writes required"

        return [
            MigrationStatement(
                9.5,
                render(attrs["data_upgrade"]),
                "-- dbwarden: irreversible"
                if op.irreversible
                else render(attrs["data_rollback"]),
                rollback_kind="irreversible" if op.irreversible else "conditional",
                rollback_reason=attrs.get("rollback_reason"),
            )
        ]
