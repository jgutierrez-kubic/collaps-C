"""Lectura de a_2_config_ingesta_a para el schema y tabla origen."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.core.catalyst.models import ConfigRow
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

CONFIG_TABLE = "a_2_config_ingesta_a"


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def load_config_rows(schema_name: str, source_table: str) -> list[ConfigRow]:
    """Lee reglas de ingesta desde {schema}.a_2_config_ingesta_a."""
    engine = get_db_engine()
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(CONFIG_TABLE)}"

    inspector = inspect(engine)
    if not inspector.has_table(CONFIG_TABLE, schema=schema_name):
        raise RuntimeError(f"Tabla de configuración no encontrada: {schema_name}.{CONFIG_TABLE}")

    columns = {col["name"] for col in inspector.get_columns(CONFIG_TABLE, schema=schema_name)}
    has_tabla_origen = "tabla_origen" in columns

    if has_tabla_origen:
        sql = text(
            f"SELECT * FROM {qualified} "
            "WHERE tabla_origen = :source_table "
            "ORDER BY columna_origen"
        )
        params = {"source_table": source_table}
    else:
        sql = text(f"SELECT * FROM {qualified} ORDER BY columna_origen")
        params = {}

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()

    config_rows = [
        ConfigRow.from_db_row(dict(row))
        for row in rows
        if row.get("columna_origen")
    ]
    if not config_rows:
        raise RuntimeError(
            f"No hay filas de configuración en {schema_name}.{CONFIG_TABLE} "
            f"para source_table='{source_table}'."
        )

    logger.info(
        "📋 [CATALYST] Config RMS cargada — schema=%s, source=%s, filas=%d",
        schema_name,
        source_table,
        len(config_rows),
    )
    return config_rows
