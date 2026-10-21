from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from alembic.operations import ops
from sqlalchemy import CheckConstraint, Enum, engine_from_config, pool

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


# Columnas Enum(native_enum=False, create_constraint=True): Alembic emite su CHECK dos veces
# (una desde los constraints de la tabla y otra al reconstruirla para renderizarla), ambas con
# el mismo nombre final por la convención, y la migración falla. Se deja una sola: la que
# genera la reconstrucción, y el tipo se renderiza sin crear la suya.


def _drop_type_bound_checks(_context: Any, _revision: Any, directives: list[Any]) -> None:
    for op in directives[0].upgrade_ops.ops:
        if isinstance(op, ops.CreateTableOp):
            op.columns = [
                c
                for c in op.columns
                if not (isinstance(c, CheckConstraint) and getattr(c, "_type_bound", False))
            ]


def _render_item(type_: str, obj: Any, _autogen_context: Any) -> str | Literal[False]:
    if type_ == "type" and isinstance(obj, Enum) and not obj.native_enum:
        values = ", ".join(repr(v) for v in obj.enums)
        return (
            f"sa.Enum({values}, name={obj.name!r}, native_enum=False, "
            f"create_constraint=False, length={obj.length})"
        )
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            process_revision_directives=_drop_type_bound_checks,
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
