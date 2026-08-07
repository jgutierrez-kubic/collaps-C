"""Generación SQL para pivotar bóveda KV vigente hacia tablas a_4_*."""

from __future__ import annotations

import logging

from app.core.catalyst.boveda_states import ESTADO_VIGENTE
from app.core.catalyst.models import ConfigRow
from app.core.catalyst.sql_types import tipo_dato_to_pg_type
from app.core.catalyst.table_contract import qualified_table

logger = logging.getLogger(__name__)


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


def _dedupe_property_columns(config_rows: list[ConfigRow]) -> list[ConfigRow]:
    """Conserva la primera ocurrencia de cada propiedad y advierte duplicados."""
    seen: set[str] = set()
    unique_rows: list[ConfigRow] = []

    for row in config_rows:
        if not row.guardar or not row.columna_origen:
            continue
        if row.columna_origen in seen:
            logger.warning(
                "⚠️ [MATERIALIZE] Propiedad duplicada en configuración ignorada: %s",
                row.columna_origen,
            )
            continue
        seen.add(row.columna_origen)
        unique_rows.append(row)

    return unique_rows


def _build_pivot_expression(columna_origen: str, tipo_dato_generico: str) -> str:
    """Construye expresión pivot con cast seguro para columnas numéricas."""
    quoted_col = _quote_ident(columna_origen)
    pg_type = tipo_dato_to_pg_type(tipo_dato_generico)
    inner_expr = (
        f"MAX(CASE WHEN propiedad_origen = {_sql_literal(columna_origen)} "
        "THEN valor_limpio END)"
    )

    if pg_type == "TEXT":
        return f"{inner_expr} AS {quoted_col}"

    safe_cast = (
        f"CASE WHEN NULLIF({inner_expr}, '') IS NULL "
        f"THEN NULL ELSE NULLIF({inner_expr}, '')::{pg_type} END"
    )
    return f"{safe_cast} AS {quoted_col}"


def build_pivot_select_sql(
    schema_name: str,
    boveda_table: str,
    *,
    source_table: str,
    config_rows: list[ConfigRow],
) -> str:
    """SELECT con pivot condicional desde bóveda VIGENTE."""
    qualified_boveda = qualified_table(schema_name, boveda_table)
    property_columns = _dedupe_property_columns(config_rows)

    pivot_exprs = [
        _build_pivot_expression(row.columna_origen, row.tipo_dato_generico)
        for row in property_columns
    ]

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
