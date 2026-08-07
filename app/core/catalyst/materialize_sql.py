"""Generación SQL para pivotar bóveda KV vigente hacia tablas a_4_*."""

from __future__ import annotations

from app.core.catalyst.boveda_states import ESTADO_VIGENTE
from app.core.catalyst.models import ConfigRow
from app.core.catalyst.sql_types import tipo_dato_to_pg_type
from app.core.catalyst.table_contract import qualified_table


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def derive_materialize_table_name(source_table: str) -> str:
    """Deriva a_4_* desde la tabla origen (ej. a_1_pma → a_4_pma)."""
    prefix = "a_1_"
    if source_table.startswith(prefix):
        return f"a_4_{source_table[len(prefix):]}"
    return f"a_4_{source_table}"


def build_pivot_select_sql(
    schema_name: str,
    boveda_table: str,
    *,
    source_table: str,
    config_rows: list[ConfigRow],
) -> str:
    """SELECT con pivot condicional desde bóveda VIGENTE."""
    qualified_boveda = qualified_table(schema_name, boveda_table)
    property_columns = [
        row for row in config_rows if row.guardar and row.columna_origen
    ]

    pivot_exprs: list[str] = []
    for row in property_columns:
        quoted_col = _quote_ident(row.columna_origen)
        pg_type = tipo_dato_to_pg_type(row.tipo_dato_generico)
        case_expr = (
            f"MAX(CASE WHEN propiedad_origen = {_sql_literal(row.columna_origen)} "
            "THEN valor_limpio END)"
        )
        if pg_type != "TEXT":
            case_expr = f"({case_expr})::{pg_type}"
        pivot_exprs.append(f"{case_expr} AS {quoted_col}")

    select_columns = [
        "entidad_interna_id",
        "MAX(llave_humana_completa) AS llave_humana_completa",
        "(ARRAY_AGG(origen_dato ORDER BY desde DESC))[1] AS origen_dato",
        "(ARRAY_AGG(creado_por ORDER BY desde DESC))[1] AS creado_por",
        "MAX(desde) AS actualizado_en",
        *pivot_exprs,
    ]

    return (
        f"SELECT {', '.join(select_columns)} "
        f"FROM {qualified_boveda} "
        f"WHERE tabla_origen = {_sql_literal(source_table)} "
        f"AND estado = {_sql_literal(ESTADO_VIGENTE)} "
        "GROUP BY entidad_interna_id"
    )


def build_materialize_ddl(
    schema_name: str,
    target_table: str,
    select_sql: str,
) -> tuple[str, str]:
    """DROP + CREATE TABLE AS para materialización idempotente."""
    qualified_target = qualified_table(schema_name, target_table)
    drop_sql = f"DROP TABLE IF EXISTS {qualified_target}"
    create_sql = f"CREATE TABLE {qualified_target} AS {select_sql}"
    return drop_sql, create_sql
