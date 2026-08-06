"""Escritura SCD2 en a_3_boveda_kv con idempotencia D11 (firma_valor)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text

from app.core.catalyst.cleanup import canonical_string
from app.core.catalyst.models import JobSummary
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

BOVEDA_TABLE = "a_3_boveda_kv"

_REQUIRED_BOVEDA_COLUMNS: dict[str, str] = {
    "clave_cotejo": "TEXT NOT NULL DEFAULT ''",
    "propiedad": "TEXT NOT NULL DEFAULT ''",
    "valor": "TEXT",
    "firma_valor": "TEXT NOT NULL DEFAULT ''",
    "traduccion_canonica": "JSONB",
    "rol": "TEXT",
    "ancla_origen": "TEXT",
    "source_table": "TEXT",
    "job_id": "TEXT",
    "desde": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "hasta": "TIMESTAMPTZ",
}


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def compute_firma_valor(valor: Any) -> str:
    """Hash SHA-256 del valor canónico para idempotencia (D11)."""
    payload = canonical_string(valor).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ensure_boveda_columns(schema_name: str) -> None:
    """Alinea columnas SCD2 en tablas bóveda existentes."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if not inspector.has_table(BOVEDA_TABLE, schema=schema_name):
        return

    existing = {col["name"] for col in inspector.get_columns(BOVEDA_TABLE, schema=schema_name)}
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(BOVEDA_TABLE)}"
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
        BOVEDA_TABLE,
        ", ".join(missing),
    )


def ensure_boveda_table(schema_name: str) -> None:
    """Crea o alinea a_3_boveda_kv con columnas SCD2 obligatorias."""
    engine = get_db_engine()
    inspector = inspect(engine)
    if inspector.has_table(BOVEDA_TABLE, schema=schema_name):
        _ensure_boveda_columns(schema_name)
        return

    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(BOVEDA_TABLE)}"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {qualified} (
        id BIGSERIAL PRIMARY KEY,
        clave_cotejo TEXT NOT NULL,
        propiedad TEXT NOT NULL,
        valor TEXT,
        firma_valor TEXT NOT NULL,
        traduccion_canonica JSONB,
        rol TEXT,
        ancla_origen TEXT,
        source_table TEXT,
        job_id TEXT,
        desde TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        hasta TIMESTAMPTZ
    )
    """
    index_activo = f"""
    CREATE INDEX IF NOT EXISTS idx_{BOVEDA_TABLE}_activo
    ON {qualified} (clave_cotejo, propiedad)
    WHERE hasta IS NULL
    """
    index_ancla = f"""
    CREATE INDEX IF NOT EXISTS idx_{BOVEDA_TABLE}_ancla_activo
    ON {qualified} (ancla_origen, propiedad)
    WHERE hasta IS NULL AND ancla_origen IS NOT NULL
    """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(index_activo))
        conn.execute(text(index_ancla))

    logger.info("🛠️ [CATALYST] Tabla bóveda creada: %s.%s", schema_name, BOVEDA_TABLE)


def upsert_boveda_record(
    schema_name: str,
    *,
    clave_cotejo: str,
    propiedad: str,
    valor: Any,
    firma_valor: str,
    traduccion_canonica: dict[str, Any] | None,
    rol: str,
    ancla_origen: str | None,
    source_table: str,
    job_id: str,
    summary: JobSummary,
    use_ancla_lookup: bool,
) -> None:
    """SCD2: cierra registro activo si cambió firma_valor; inserta si es nuevo o cambió."""
    engine = get_db_engine()
    qualified = f"{_quote_ident(schema_name)}.{_quote_ident(BOVEDA_TABLE)}"
    valor_text = canonical_string(valor)
    traduccion_json = json.dumps(traduccion_canonica) if traduccion_canonica else None
    now = datetime.now(timezone.utc)

    if use_ancla_lookup:
        if not ancla_origen:
            raise ValueError("ancla_origen es requerida para búsqueda SCD2 por ancla.")
        select_sql = text(
            f"SELECT firma_valor FROM {qualified} "
            "WHERE ancla_origen = :ancla_origen AND propiedad = :propiedad AND hasta IS NULL "
            "LIMIT 1"
        )
        lookup_params = {"ancla_origen": ancla_origen, "propiedad": propiedad}
        close_sql = text(
            f"UPDATE {qualified} SET hasta = :hasta "
            "WHERE ancla_origen = :ancla_origen AND propiedad = :propiedad AND hasta IS NULL"
        )
    else:
        select_sql = text(
            f"SELECT firma_valor FROM {qualified} "
            "WHERE clave_cotejo = :clave_cotejo AND propiedad = :propiedad AND hasta IS NULL "
            "LIMIT 1"
        )
        lookup_params = {"clave_cotejo": clave_cotejo, "propiedad": propiedad}
        close_sql = text(
            f"UPDATE {qualified} SET hasta = :hasta "
            "WHERE clave_cotejo = :clave_cotejo AND propiedad = :propiedad AND hasta IS NULL"
        )

    insert_sql = text(
        f"INSERT INTO {qualified} "
        "(clave_cotejo, propiedad, valor, firma_valor, traduccion_canonica, rol, "
        "ancla_origen, source_table, job_id, desde) "
        "VALUES (:clave_cotejo, :propiedad, :valor, :firma_valor, "
        "CAST(:traduccion_canonica AS JSONB), :rol, :ancla_origen, :source_table, "
        ":job_id, :desde)"
    )

    with engine.begin() as conn:
        current = conn.execute(select_sql, lookup_params).mappings().first()

        if current and current["firma_valor"] == firma_valor:
            summary.registros_sin_cambio += 1
            return

        close_params = {**lookup_params, "hasta": now}
        if current:
            conn.execute(close_sql, close_params)
            summary.registros_actualizados += 1
        else:
            summary.registros_insertados += 1

        conn.execute(
            insert_sql,
            {
                "clave_cotejo": clave_cotejo,
                "propiedad": propiedad,
                "valor": valor_text,
                "firma_valor": firma_valor,
                "traduccion_canonica": traduccion_json,
                "rol": rol,
                "ancla_origen": ancla_origen,
                "source_table": source_table,
                "job_id": job_id,
                "desde": now,
            },
        )
