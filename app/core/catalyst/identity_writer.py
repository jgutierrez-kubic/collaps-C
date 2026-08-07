"""Registro y ciclo de vida de entidades en a_3_identidad (Capa 3)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import bindparam, inspect, text

from app.core.catalyst.boveda_states import ESTADO_ELIMINADO, ESTADO_VIGENTE
from app.core.catalyst.entity_states import ESTADO_ENTIDAD_ACTIVO, ESTADO_ENTIDAD_INACTIVO
from app.core.catalyst.models import JobSummary
from app.core.catalyst.table_contract import qualified_table, table_index_suffix
from app.core.db import get_db_engine

logger = logging.getLogger(__name__)

_REQUIRED_IDENTIDAD_COLUMNS: dict[str, str] = {
    "llave_humana_completa": "TEXT NOT NULL DEFAULT ''",
    "tabla_origen": "TEXT NOT NULL DEFAULT ''",
    "estado_entidad": "TEXT NOT NULL DEFAULT 'ACTIVO'",
    "total_propiedades_activas": "INTEGER NOT NULL DEFAULT 0",
    "actualizado_en": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "job_id": "TEXT",
}


def _ensure_identidad_columns(schema_name: str, identidad_table: str) -> None:
    """Alinea columnas del inventario maestro en tablas de identidad existentes."""
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
    """Crea o alinea a_3_identidad en el schema del job."""
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
        estado_entidad TEXT NOT NULL DEFAULT '{ESTADO_ENTIDAD_ACTIVO}',
        total_propiedades_activas INTEGER NOT NULL DEFAULT 0,
        actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        job_id TEXT
    )
    """
    index_llave = f"""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_{index_suffix}_llave_origen
    ON {qualified} (llave_humana_completa, tabla_origen)
    """
    index_activos = f"""
    CREATE INDEX IF NOT EXISTS idx_{index_suffix}_activos
    ON {qualified} (tabla_origen, estado_entidad)
    WHERE estado_entidad = '{ESTADO_ENTIDAD_ACTIVO}'
    """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(index_llave))
        conn.execute(text(index_activos))

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
    """Marca entidad como ACTIVO y vincula el job_id de la ejecución actual."""
    if not llave_humana_completa.strip():
        raise ValueError("llave_humana_completa es obligatoria en a_3_identidad.")

    qualified = qualified_table(schema_name, identidad_table)
    now = datetime.now(timezone.utc)
    sql = text(
        f"INSERT INTO {qualified} "
        "(entidad_interna_id, llave_humana_completa, tabla_origen, estado_entidad, "
        "actualizado_en, job_id) "
        "VALUES (:entidad_interna_id, :llave_humana_completa, :tabla_origen, "
        ":estado_activo, :actualizado_en, :job_id) "
        "ON CONFLICT (entidad_interna_id) DO UPDATE SET "
        "llave_humana_completa = EXCLUDED.llave_humana_completa, "
        "tabla_origen = EXCLUDED.tabla_origen, "
        "estado_entidad = EXCLUDED.estado_entidad, "
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
                "estado_activo": ESTADO_ENTIDAD_ACTIVO,
                "actualizado_en": now,
                "job_id": job_id,
            },
        )


def refresh_total_propiedades_activas(
    schema_name: str,
    identidad_table: str,
    boveda_table: str,
    *,
    entidad_interna_id: str,
    tabla_origen: str,
) -> None:
    """Actualiza total_propiedades_activas según filas VIGENTES en bóveda."""
    identidad_qualified = qualified_table(schema_name, identidad_table)
    boveda_qualified = qualified_table(schema_name, boveda_table)
    sql = text(
        f"UPDATE {identidad_qualified} "
        "SET total_propiedades_activas = ( "
        f"  SELECT COUNT(*) FROM {boveda_qualified} "
        "  WHERE entidad_interna_id = :entidad_interna_id "
        "  AND tabla_origen = :tabla_origen "
        "  AND estado = :estado_vigente "
        ") "
        "WHERE entidad_interna_id = :entidad_interna_id"
    )
    with get_db_engine().begin() as conn:
        conn.execute(
            sql,
            {
                "entidad_interna_id": entidad_interna_id,
                "tabla_origen": tabla_origen,
                "estado_vigente": ESTADO_VIGENTE,
            },
        )


def finalize_entity_lifecycle(
    schema_name: str,
    identidad_table: str,
    boveda_table: str,
    *,
    tabla_origen: str,
    job_id: str,
    summary: JobSummary,
) -> None:
    """Cierra entidades no procesadas en el job y sincroniza bóveda + resumen."""
    identidad_qualified = qualified_table(schema_name, identidad_table)
    boveda_qualified = qualified_table(schema_name, boveda_table)
    now = datetime.now(timezone.utc)

    select_inactivas_sql = text(
        f"SELECT entidad_interna_id FROM {identidad_qualified} "
        "WHERE tabla_origen = :tabla_origen "
        "AND job_id IS DISTINCT FROM :job_id"
    )
    mark_inactivas_sql = text(
        f"UPDATE {identidad_qualified} "
        "SET estado_entidad = :estado_inactivo, "
        "total_propiedades_activas = 0, "
        "actualizado_en = :actualizado_en "
        "WHERE tabla_origen = :tabla_origen "
        "AND job_id IS DISTINCT FROM :job_id"
    )

    with get_db_engine().begin() as conn:
        inactivas = conn.execute(
            select_inactivas_sql,
            {"tabla_origen": tabla_origen, "job_id": job_id},
        ).scalars().all()
        inactiva_ids = [str(entity_id) for entity_id in inactivas]

        if inactiva_ids:
            conn.execute(
                mark_inactivas_sql,
                {
                    "estado_inactivo": ESTADO_ENTIDAD_INACTIVO,
                    "actualizado_en": now,
                    "tabla_origen": tabla_origen,
                    "job_id": job_id,
                },
            )

            eliminar_boveda_sql = (
                text(
                    f"UPDATE {boveda_qualified} "
                    "SET estado = :estado_eliminado, hasta = :hasta, job_id = :job_id "
                    "WHERE tabla_origen = :tabla_origen "
                    "AND estado = :estado_vigente "
                    "AND entidad_interna_id IN :inactiva_ids"
                ).bindparams(bindparam("inactiva_ids", expanding=True))
            )
            boveda_result = conn.execute(
                eliminar_boveda_sql,
                {
                    "estado_eliminado": ESTADO_ELIMINADO,
                    "hasta": now,
                    "job_id": job_id,
                    "tabla_origen": tabla_origen,
                    "estado_vigente": ESTADO_VIGENTE,
                    "inactiva_ids": inactiva_ids,
                },
            )
            summary.registros_eliminados += int(boveda_result.rowcount or 0)
            summary.entidades_inactivadas += len(inactiva_ids)

        refresh_activas_sql = text(
            f"UPDATE {identidad_qualified} AS identidad "
            "SET total_propiedades_activas = COALESCE(conteo.total, 0), "
            "actualizado_en = :actualizado_en "
            "FROM ( "
            f"  SELECT entidad_interna_id, COUNT(*) AS total FROM {boveda_qualified} "
            "  WHERE tabla_origen = :tabla_origen AND estado = :estado_vigente "
            "  GROUP BY entidad_interna_id "
            ") AS conteo "
            "WHERE identidad.entidad_interna_id = conteo.entidad_interna_id "
            "AND identidad.tabla_origen = :tabla_origen "
            "AND identidad.job_id = :job_id"
        )
        conn.execute(
            refresh_activas_sql,
            {
                "actualizado_en": now,
                "tabla_origen": tabla_origen,
                "estado_vigente": ESTADO_VIGENTE,
                "job_id": job_id,
            },
        )

    if summary.entidades_inactivadas:
        logger.info(
            "📦 [CATALYST] Entidades INACTIVO — tabla_origen=%s, total=%d, boveda_eliminados=%d",
            tabla_origen,
            summary.entidades_inactivadas,
            summary.registros_eliminados,
        )
