"""Lectura de tabla de configuración de ingesta para el schema y tabla origen."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.core.catalyst.models import ConfigRow
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def _resolve_column_name(columns: set[str], *candidates: str) -> str | None:
    lower_map = {name.lower(): name for name in columns}
    for candidate in candidates:
        match = lower_map.get(candidate.lower())
        if match:
            return match
    return None


def load_config_rows(
    schema_name: str,
    source_table: str,
    config_table: str,
) -> list[ConfigRow]:
    """Lee reglas de ingesta desde la tabla de configuración del job."""
    engine = get_db_engine()
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(config_table)}"

    inspector = inspect(engine)
    if not inspector.has_table(config_table, schema=schema_name):
        raise RuntimeError(f"Tabla de configuración no encontrada: {schema_name}.{config_table}")

    columns = {col["name"] for col in inspector.get_columns(config_table, schema=schema_name)}
    columna_field = _resolve_column_name(columns, "columna_origen", "columnaOrigen", "propiedad")
    if not columna_field:
        raise RuntimeError(
            f"La tabla {schema_name}.{config_table} no expone columna_origen/propiedad."
        )

    tabla_filter_field = _resolve_column_name(
        columns, "tabla", "tabla_origen", "tablaOrigen"
    )
    if not tabla_filter_field:
        raise RuntimeError(
            f"La tabla {schema_name}.{config_table} no expone columna tabla/tabla_origen "
            "para filtrar por source_table."
        )

    order_clause = f'ORDER BY {_quote_ident(columna_field)}'
    sql = text(
        f"SELECT * FROM {qualified} "
        f"WHERE {_quote_ident(tabla_filter_field)} = :source_table "
        f"{order_clause}"
    )
    params = {"source_table": source_table}

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()

    config_rows: list[ConfigRow] = []
    for row in rows:
        config = ConfigRow.from_db_row(dict(row))
        if config.columna_origen:
            config_rows.append(config)
    if not config_rows:
        raise RuntimeError(
            f"No hay filas de configuración en {schema_name}.{config_table} "
            f"para source_table='{source_table}'."
        )

    logger.info(
        "📋 [CATALYST] Config RMS cargada — schema=%s, config=%s, source=%s, filas=%d",
        schema_name,
        config_table,
        source_table,
        len(config_rows),
    )
    return config_rows
