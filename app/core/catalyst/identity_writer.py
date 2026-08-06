"""Registro persistente UUID <-> llave_humana_completa en tabla de identidad."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app.core.catalyst.table_contract import qualified_table, table_index_suffix
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

_REQUIRED_IDENTIDAD_COLUMNS: dict[str, str] = {
    "llave_humana_completa": "TEXT NOT NULL DEFAULT ''",
    "tabla_origen": "TEXT NOT NULL DEFAULT ''",
    "actualizado_en": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "job_id": "TEXT",
}


def _ensure_identidad_columns(schema_name: str, identidad_table: str) -> None:
    """Alinea columnas del modelo RMS en tablas de identidad existentes."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if not inspector.has_table(identidad_table, schema=schema_name):
        return

    existing = {
        col["name"] for col in inspector.get_columns(identidad_table, schema=schema_name)
    }
    qualified = qualified_table(schema_name, identidad_table)
    missing = {
        name: ddl
        for name, ddl in _REQUIRED_IDENTIDAD_COLUMNS.items()
        if name not in existing
    }
    if not missing:
        return

    with engine.begin() as conn:
        for column, column_type in missing.items():
            ddl = (
                f"ALTER TABLE {qualified} "
                f'ADD COLUMN IF NOT EXISTS "{column}" {column_type}'
            )
            conn.execute(text(ddl))

    logger.info(
        "🛠️ [CATALYST] Columnas identidad alineadas en %s.%s: %s",
        schema_name,
        identidad_table,
        ", ".join(missing),
    )


def ensure_identidad_table(schema_name: str, identidad_table: str) -> None:
    """Crea o alinea la tabla de identidad en el schema del job."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if inspector.has_table(identidad_table, schema=schema_name):
        _ensure_identidad_columns(schema_name, identidad_table)
        return

    index_suffix = table_index_suffix(identidad_table)
    qualified = qualified_table(schema_name, identidad_table)
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {qualified} (
        entidad_interna_id TEXT PRIMARY KEY,
        llave_humana_completa TEXT NOT NULL,
        tabla_origen TEXT NOT NULL,
        actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        job_id TEXT
    )
    """
    index_llave = f"""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_{index_suffix}_llave_origen
    ON {qualified} (llave_humana_completa, tabla_origen)
    """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(index_llave))

    logger.info("🛠️ [CATALYST] Tabla identidad creada: %s.%s", schema_name, identidad_table)


def lookup_entidad_interna_id(
    schema_name: str,
    identidad_table: str,
    *,
    llave_humana_completa: str,
    tabla_origen: str,
) -> str | None:
    """Recupera entidad_interna_id existente para llave + tabla_origen."""
    inspector = inspect(get_db_engine())
    if not inspector.has_table(identidad_table, schema=schema_name):
        return None

    qualified = qualified_table(schema_name, identidad_table)
    sql = text(
        f"SELECT entidad_interna_id FROM {qualified} "
        "WHERE llave_humana_completa = :llave_humana_completa "
        "AND tabla_origen = :tabla_origen "
        "LIMIT 1"
    )
    with get_db_engine().connect() as conn:
        row = conn.execute(
            sql,
            {
                "llave_humana_completa": llave_humana_completa,
                "tabla_origen": tabla_origen,
            },
        ).mappings().first()
    return None if row is None else str(row["entidad_interna_id"])


def upsert_identidad(
    schema_name: str,
    identidad_table: str,
    *,
    entidad_interna_id: str,
    llave_humana_completa: str,
    tabla_origen: str,
    job_id: str,
) -> None:
    """Persiste identidad con actualizado_en y job_id de la ejecución actual."""
    qualified = qualified_table(schema_name, identidad_table)
    now = datetime.now(timezone.utc)
    sql = text(
        f"INSERT INTO {qualified} "
        "(entidad_interna_id, llave_humana_completa, tabla_origen, actualizado_en, job_id) "
        "VALUES (:entidad_interna_id, :llave_humana_completa, :tabla_origen, "
        ":actualizado_en, :job_id) "
        "ON CONFLICT (entidad_interna_id) DO UPDATE SET "
        "llave_humana_completa = EXCLUDED.llave_humana_completa, "
        "tabla_origen = EXCLUDED.tabla_origen, "
        "actualizado_en = EXCLUDED.actualizado_en, "
        "job_id = EXCLUDED.job_id"
    )

    with get_db_engine().begin() as conn:
        conn.execute(
            sql,
            {
                "entidad_interna_id": entidad_interna_id,
                "llave_humana_completa": llave_humana_completa,
                "tabla_origen": tabla_origen,
                "actualizado_en": now,
                "job_id": job_id,
            },
        )
