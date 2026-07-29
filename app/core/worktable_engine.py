"""Motor de worktables — esqueleto para materialización agrupada desde PostgreSQL."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import text

from app.core.analysis_engine import AnalysisEngine
from app.core.config import DB_URL
from app.core.db import get_db_engine
from app.models.worktable_payload import WorktableCreatePayload

logger = logging.getLogger(__name__)


class WorktableEngine:
    def __init__(self, payload: WorktableCreatePayload) -> None:
        self.payload = payload

    def _build_source_query(self) -> str:
        """Construye la consulta SQL de lectura/agrupación sobre la tabla origen.

        TODO: Traducir group_by_columns y order_by_rules a SQL seguro con
        identificadores ya validados por Pydantic (evitar SQL injection).
        TODO: Definir agregaciones (SUM, COUNT, AVG, etc.) según reglas de negocio.
        """
        schema = self.payload.schema_name
        source = self.payload.source_table
        group_cols = [
            col.strip() for col in self.payload.group_by_columns.split(",") if col.strip()
        ]
        order_rules = [
            rule.strip() for rule in self.payload.order_by_rules.split(",") if rule.strip()
        ]

        quoted_group = ", ".join(f'"{col}"' for col in group_cols)
        quoted_order = ", ".join(
            " ".join(part for part in rule.split()) for rule in order_rules
        )

        return (
            f"SELECT {quoted_group}\n"
            f'FROM "{schema}"."{source}"\n'
            f"GROUP BY {quoted_group}\n"
            f"ORDER BY {quoted_order}"
        )

    def _load_source_data(self) -> pd.DataFrame:
        """Lee y agrupa datos desde PostgreSQL.

        Opción A (recomendada): ejecutar SQL agregado directamente en Postgres.
        Opción B (alternativa): pd.read_sql tabla completa + df.groupby() en Pandas.
        """
        if not DB_URL:
            raise RuntimeError("DATABASE_URL no está configurada.")

        sql = self._build_source_query()
        logger.info("📐 [WORKTABLE] Query de origen:\n%s", sql)

        engine = get_db_engine()
        with engine.connect() as connection:
            return pd.read_sql(text(sql), con=connection)

    def _stamp_worktable_metadata(
        self,
        df: pd.DataFrame,
        run_id: int,
        created_at: datetime,
        job_id: str,
    ) -> pd.DataFrame:
        """Inyecta run_id incremental, timestamp y job_id a todas las filas del lote."""
        df_stamped = df.copy()
        df_stamped["run_id"] = run_id
        df_stamped["created_at"] = created_at
        df_stamped["timestamp"] = created_at
        df_stamped["job_id"] = job_id
        df_stamped["source"] = "worktable"
        return AnalysisEngine._reorder_columns_for_persist(df_stamped)

    def _auto_migrate_table(
        self,
        engine,
        schema_name: str,
        table_name: str,
        df: pd.DataFrame,
    ) -> None:
        """Alinea el esquema destino con las columnas del DataFrame (ALTER TABLE IF NOT EXISTS)."""
        if not AnalysisEngine._table_exists(engine, schema_name, table_name):
            logger.info(
                "🛠️ [WORKTABLE] Tabla %s.%s no existe — se creará en el primer to_sql.",
                schema_name,
                table_name,
            )
            return

        existing = AnalysisEngine._get_existing_columns(engine, schema_name, table_name)
        new_columns = [col for col in df.columns if col not in existing]
        if not new_columns:
            return

        qualified = f'"{schema_name}"."{table_name}"'
        with engine.begin() as conn:
            for column in new_columns:
                pg_type = AnalysisEngine._pandas_dtype_to_pg(df[column])
                ddl = (
                    f"ALTER TABLE {qualified} "
                    f'ADD COLUMN IF NOT EXISTS "{column}" {pg_type}'
                )
                logger.info(
                    "🛠️ [WORKTABLE] Agregando columna '%s' (%s) a %s.%s",
                    column,
                    pg_type,
                    schema_name,
                    table_name,
                )
                conn.execute(text(ddl))

    def run(self, job_id: str | None = None) -> dict[str, Any]:
        job_id = job_id or str(uuid4())
        created_at = datetime.now(timezone.utc)
        started_at = time.perf_counter()

        logger.info(
            "🐍 [WORKTABLE START] job_id=%s, source=%s → target=%s",
            job_id,
            self.payload.source_table,
            self.payload.target_table,
        )

        try:
            # Paso 1: Leer/agrupar datos desde Postgres (o Pandas).
            df = self._load_source_data()

            # Paso 2: Asignar run_id incremental y timestamp por fila.
            schema_name = self.payload.schema_name
            table_name = self.payload.target_table
            engine = get_db_engine()
            run_id = AnalysisEngine._allocate_next_run_id(engine, schema_name, table_name)
            df_ready = self._stamp_worktable_metadata(df, run_id, created_at, job_id)

            # Paso 3: Crear tabla física si no existe y persistir (append).
            self._auto_migrate_table(engine, schema_name, table_name, df_ready)
            df_ready.to_sql(
                name=table_name,
                schema=schema_name,
                con=engine,
                if_exists="append",
                index=False,
            )
            AnalysisEngine._add_directus_primary_key(engine, schema_name, table_name)

            # Paso 4: Auto-registro en Directus (multi-tenant, idempotente).
            AnalysisEngine._register_directus_collection(schema_name, table_name)

            # TODO: Paso 5 — callback HTTP a n8n (mismo patrón que AnalysisEngine._send_callback).

            elapsed = time.perf_counter() - started_at
            logger.info(
                "✅ [WORKTABLE DONE] %.2f s — job_id=%s, run_id=%d, filas=%d",
                elapsed,
                job_id,
                run_id,
                len(df_ready),
            )
            return {
                "status": "success",
                "jobId": job_id,
                "runId": run_id,
                "targetTable": f"{schema_name}.{table_name}",
                "rows": len(df_ready),
            }
        except Exception as exc:
            logger.error("❌ [WORKTABLE ERROR] %s", exc, exc_info=True)
            return {"status": "failed", "jobId": job_id, "error": str(exc)}
