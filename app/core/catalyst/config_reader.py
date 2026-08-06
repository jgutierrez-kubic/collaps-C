"""Lectura de a_2_config para el schema y tabla origen."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.core.catalyst.models import ConfigRow
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

A2_CONFIG_TABLE = "a_2_config"


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def load_config_rows(schema_name: str, source_table: str) -> list[ConfigRow]:
    """Lee el mapeo desde {schema}.a_2_config filtrado por tabla origen si aplica."""
    engine = get_db_engine()
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(A2_CONFIG_TABLE)}"

    inspector = inspect(engine)
    if not inspector.has_table(A2_CONFIG_TABLE, schema=schema_name):
        raise RuntimeError(f"Tabla de configuración no encontrada: {schema_name}.{A2_CONFIG_TABLE}")

    columns = {col["name"] for col in inspector.get_columns(A2_CONFIG_TABLE, schema=schema_name)}
    has_tabla_origen = "tabla_origen" in columns

    if has_tabla_origen:
        sql = text(
            f"SELECT * FROM {qualified} "
            "WHERE tabla_origen = :source_table "
            "ORDER BY orden_llave NULLS LAST, propiedad"
        )
        params = {"source_table": source_table}
    else:
        sql = text(f"SELECT * FROM {qualified} ORDER BY orden_llave NULLS LAST, propiedad")
        params = {}

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()

    config_rows = [ConfigRow.from_db_row(dict(row)) for row in rows if row.get("propiedad")]
    if not config_rows:
        raise RuntimeError(
            f"No hay filas de configuración en {schema_name}.{A2_CONFIG_TABLE} "
            f"para source_table='{source_table}'."
        )

    logger.info(
        "📋 [CATALYST] Config cargada — schema=%s, source=%s, filas=%d",
        schema_name,
        source_table,
        len(config_rows),
    )
    return config_rows
