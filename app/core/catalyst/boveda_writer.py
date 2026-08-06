"""Escritura SCD2 en bóveda KV con firma_auditoria (RMS Genérico v1.4)."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text

from app.core.catalyst.cleanup import canonical_string
from app.core.catalyst.models import JobSummary
from app.core.catalyst.table_contract import table_index_suffix
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

_REQUIRED_BOVEDA_COLUMNS: dict[str, str] = {
    "entidad_interna_id": "TEXT NOT NULL DEFAULT ''",
    "propiedad_origen": "TEXT NOT NULL DEFAULT ''",
    "valor_original": "TEXT",
    "valor_limpio": "TEXT",
    "firma_auditoria": "TEXT NOT NULL DEFAULT ''",
    "tipo_dato_generico": "TEXT",
    "tabla_origen": "TEXT",
    "job_id": "TEXT",
    "desde": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "hasta": "TIMESTAMPTZ",
}


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def compute_firma_auditoria(valor_limpio: str | None) -> str:
    """Hash SHA-256 del valor_limpio (NULL → firma de cadena vacía)."""
    payload = canonical_string(valor_limpio).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ensure_boveda_columns(schema_name: str, boveda_table: str) -> None:
    """Alinea columnas SCD2 en tablas bóveda existentes."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if not inspector.has_table(boveda_table, schema=schema_name):
        return

    existing = {col["name"] for col in inspector.get_columns(boveda_table, schema=schema_name)}
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(boveda_table)}"
    missing = {
        name: ddl
        for name, ddl in _REQUIRED_BOVEDA_COLUMNS.items()
        if name not in existing
    }
    if not missing:
        return

    with engine.begin() as conn:
        for column, column_type in missing.items():
            ddl = (
                f"ALTER TABLE {qualified} "
                f"ADD COLUMN IF NOT EXISTS {_quote_ident(column)} {column_type}"
            )
            conn.execute(text(ddl))

    logger.info(
        "🛠️ [CATALYST] Columnas SCD2 alineadas en %s.%s: %s",
        schema_name,
        boveda_table,
        ", ".join(missing),
    )


def ensure_boveda_table(schema_name: str, boveda_table: str) -> None:
    """Crea o alinea la bóveda KV con columnas RMS v1.4."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if inspector.has_table(boveda_table, schema=schema_name):
        _ensure_boveda_columns(schema_name, boveda_table)
        return

    index_suffix = table_index_suffix(boveda_table)
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(boveda_table)}"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {qualified} (
        id BIGSERIAL PRIMARY KEY,
        entidad_interna_id TEXT NOT NULL,
        propiedad_origen TEXT NOT NULL,
        valor_original TEXT,
        valor_limpio TEXT,
        firma_auditoria TEXT NOT NULL,
        tipo_dato_generico TEXT,
        tabla_origen TEXT,
        job_id TEXT,
        desde TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        hasta TIMESTAMPTZ
    )
    """
    index_activo = f"""
    CREATE INDEX IF NOT EXISTS idx_{index_suffix}_activo
    ON {qualified} (entidad_interna_id, propiedad_origen)
    WHERE hasta IS NULL
    """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(index_activo))

    logger.info("🛠️ [CATALYST] Tabla bóveda creada: %s.%s", schema_name, boveda_table)


def upsert_boveda_record(
    schema_name: str,
    boveda_table: str,
    *,
    entidad_interna_id: str,
    propiedad_origen: str,
    valor_original: str | None,
    valor_limpio: str | None,
    firma_auditoria: str,
    tipo_dato_generico: str,
    tabla_origen: str,
    job_id: str,
    summary: JobSummary,
) -> None:
    """SCD2 inmutable: cierra versión anterior si cambió firma_auditoria."""
    engine = get_db_engine()
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(boveda_table)}"
    now = datetime.now(timezone.utc)

    select_sql = text(
        f"SELECT firma_auditoria FROM {qualified} "
        "WHERE entidad_interna_id = :entidad_interna_id "
        "AND propiedad_origen = :propiedad_origen AND hasta IS NULL "
        "LIMIT 1"
    )
    close_sql = text(
        f"UPDATE {qualified} SET hasta = :hasta "
        "WHERE entidad_interna_id = :entidad_interna_id "
        "AND propiedad_origen = :propiedad_origen AND hasta IS NULL"
    )
    insert_sql = text(
        f"INSERT INTO {qualified} "
        "(entidad_interna_id, propiedad_origen, valor_original, valor_limpio, "
        "firma_auditoria, tipo_dato_generico, tabla_origen, job_id, desde, hasta) "
        "VALUES (:entidad_interna_id, :propiedad_origen, :valor_original, :valor_limpio, "
        ":firma_auditoria, :tipo_dato_generico, :tabla_origen, :job_id, :desde, NULL)"
    )

    lookup_params = {
        "entidad_interna_id": entidad_interna_id,
        "propiedad_origen": propiedad_origen,
    }

    with engine.begin() as conn:
        current = conn.execute(select_sql, lookup_params).mappings().first()

        if current and current["firma_auditoria"] == firma_auditoria:
            summary.registros_sin_cambio += 1
            return

        if current:
            conn.execute(close_sql, {**lookup_params, "hasta": now})
            summary.registros_actualizados += 1
        else:
            summary.registros_insertados += 1

        conn.execute(
            insert_sql,
            {
                "entidad_interna_id": entidad_interna_id,
                "propiedad_origen": propiedad_origen,
                "valor_original": valor_original,
                "valor_limpio": valor_limpio,
                "firma_auditoria": firma_auditoria,
                "tipo_dato_generico": tipo_dato_generico,
                "tabla_origen": tabla_origen,
                "job_id": job_id,
                "desde": now,
            },
        )
