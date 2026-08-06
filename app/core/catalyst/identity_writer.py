"""Registro persistente UUID <-> llave_humana_completa en a_2_identidad."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

IDENTIDAD_TABLE = "a_2_identidad"


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def ensure_identidad_table(schema_name: str) -> None:
    """Crea a_2_identidad si no existe."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if inspector.has_table(IDENTIDAD_TABLE, schema=schema_name):
        return

    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(IDENTIDAD_TABLE)}"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {qualified} (
        entidad_interna_id TEXT PRIMARY KEY,
        llave_humana_completa TEXT NOT NULL,
        tabla_origen TEXT,
        actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """
    index_llave = f"""
    CREATE INDEX IF NOT EXISTS idx_{IDENTIDAD_TABLE}_llave
    ON {qualified} (llave_humana_completa)
    """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(index_llave))

    logger.info("🛠️ [CATALYST] Tabla identidad creada: %s.%s", schema_name, IDENTIDAD_TABLE)


def upsert_identidad(
    schema_name: str,
    *,
    entidad_interna_id: str,
    llave_humana_completa: str,
    tabla_origen: str,
) -> None:
    """Persiste o actualiza la relación UUID <-> llave humana completa."""
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(IDENTIDAD_TABLE)}"
    now = datetime.now(timezone.utc)
    sql = text(
        f"INSERT INTO {qualified} "
        "(entidad_interna_id, llave_humana_completa, tabla_origen, actualizado_en) "
        "VALUES (:entidad_interna_id, :llave_humana_completa, :tabla_origen, :actualizado_en) "
        "ON CONFLICT (entidad_interna_id) DO UPDATE SET "
        "llave_humana_completa = EXCLUDED.llave_humana_completa, "
        "tabla_origen = EXCLUDED.tabla_origen, "
        "actualizado_en = EXCLUDED.actualizado_en"
    )

    with get_db_engine().begin() as conn:
        conn.execute(
            sql,
            {
                "entidad_interna_id": entidad_interna_id,
                "llave_humana_completa": llave_humana_completa,
                "tabla_origen": tabla_origen,
                "actualizado_en": now,
            },
        )
