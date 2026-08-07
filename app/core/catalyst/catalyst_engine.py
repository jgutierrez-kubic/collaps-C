"""Orquestador principal del refiner Catalyst (RMS Genérico v1.5)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator
from uuid import uuid4

import httpx
import pandas as pd
from sqlalchemy import inspect, text
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.analysis_engine import (
    _CALLBACK_MAX_ATTEMPTS,
    _is_retryable_callback_error,
    _log_callback_retry,
)
from app.core.catalyst.origen_dato import ORIGEN_CARGA_MASIVA
from app.core.catalyst.boveda_writer import (
    compute_firma_auditoria,
    ensure_boveda_table,
    upsert_boveda_record,
)
from app.core.catalyst.cleanup import to_valor_limpio, to_valor_original
from app.core.catalyst.config_reader import load_config_rows
from app.core.catalyst.governance import (
    REQ_ACEPTADO_COLUMN,
    row_passes_acceptance_filter,
    source_table_has_column,
)
from app.core.catalyst.identity import resolve_row_identity
from app.core.catalyst.identity_writer import (
    ensure_identidad_table,
    finalize_entity_lifecycle,
    refresh_total_propiedades_activas,
    upsert_identidad,
)
from app.core.catalyst.models import ConfigRow, JobSummary
from app.core.config import DB_URL
from app.core.db import get_db_engine
from app.models.catalyst_payload import CatalystJobPayload

logger = logging.getLogger(__name__)

SQL_CHUNK_SIZE = int(os.getenv("SQL_CHUNK_SIZE", "10000"))


class CatalystEngine:
    def __init__(self, payload: CatalystJobPayload) -> None:
        self.payload = payload
        self.tables = payload.resolve_tables()
        self._job_id: str | None = None
        self._summary = JobSummary()
        self._has_req_aceptado = False

    def _quote_ident(self, name: str) -> str:
        return f'"{name}"'

    def _inspect_source_table(self) -> None:
        inspector = inspect(get_db_engine())
        columns = {
            col["name"]
            for col in inspector.get_columns(
                self.payload.source_table,
                schema=self.payload.schema_name,
            )
        }
        self._has_req_aceptado = source_table_has_column(columns, REQ_ACEPTADO_COLUMN)

    def _count_rejected_rows(self) -> int:
        if not self._has_req_aceptado:
            return 0

        schema_name = self.payload.schema_name
        source_table = self.payload.source_table
        qualified = f"{self._quote_ident(schema_name)}.{self._quote_ident(source_table)}"
        count_sql = text(
            f"SELECT COUNT(*) AS total FROM {qualified} "
            f"WHERE {self._quote_ident(REQ_ACEPTADO_COLUMN)} IS DISTINCT FROM TRUE"
        )
        with get_db_engine().connect() as connection:
            result = connection.execute(count_sql).mappings().first()
        return int(result["total"]) if result else 0

    def _iter_source_chunks(self, chunksize: int = SQL_CHUNK_SIZE) -> Iterator[pd.DataFrame]:
        schema_name = self.payload.schema_name
        source_table = self.payload.source_table
        qualified = f"{self._quote_ident(schema_name)}.{self._quote_ident(source_table)}"
        where_clause = ""
        if self._has_req_aceptado:
            where_clause = f" WHERE {self._quote_ident(REQ_ACEPTADO_COLUMN)} IS TRUE"

        engine = get_db_engine()
        offset = 0

        while True:
            chunk_sql = text(
                f"SELECT * FROM {qualified} AS _source_chunk"
                f"{where_clause} "
                "LIMIT :limit OFFSET :offset"
            )
            with engine.connect() as connection:
                chunk = pd.read_sql(
                    chunk_sql,
                    con=connection,
                    params={"limit": chunksize, "offset": offset},
                )

            if chunk.empty:
                break

            yield chunk

            if len(chunk) < chunksize:
                break
            offset += chunksize

    def _process_row(
        self,
        row: dict[str, Any],
        persist_columns: list[ConfigRow],
        key_columns: list[ConfigRow],
        job_id: str,
    ) -> None:
        if not row_passes_acceptance_filter(row, has_req_aceptado=self._has_req_aceptado):
            self._summary.filas_omitidas += 1
            return

        identity = resolve_row_identity(
            row,
            key_columns,
            self.payload.separador_llave,
            schema_name=self.payload.schema_name,
            identidad_table=self.tables.identidad_table,
            tabla_origen=self.payload.source_table,
        )

        upsert_identidad(
            self.payload.schema_name,
            self.tables.identidad_table,
            entidad_interna_id=identity.entidad_interna_id,
            llave_humana_completa=identity.llave_humana_completa,
            tabla_origen=self.payload.source_table,
            job_id=job_id,
        )

        for config in persist_columns:
            if not config.guardar:
                continue
            if config.columna_origen not in row:
                continue

            raw_value = row.get(config.columna_origen)
            valor_original = to_valor_original(raw_value)
            valor_limpio = to_valor_limpio(raw_value, config.tipo_dato_generico)
            firma = compute_firma_auditoria(valor_limpio)

            upsert_boveda_record(
                self.payload.schema_name,
                self.tables.boveda_table,
                entidad_interna_id=identity.entidad_interna_id,
                llave_humana_completa=identity.llave_humana_completa,
                propiedad_origen=config.columna_origen,
                valor_original=valor_original,
                valor_limpio=valor_limpio,
                firma_auditoria=firma,
                tipo_dato_generico=config.tipo_dato_generico,
                tabla_origen=self.payload.source_table,
                job_id=job_id,
                summary=self._summary,
                origen_dato=ORIGEN_CARGA_MASIVA,
            )

        refresh_total_propiedades_activas(
            self.payload.schema_name,
            self.tables.identidad_table,
            self.tables.boveda_table,
            entidad_interna_id=identity.entidad_interna_id,
            tabla_origen=self.payload.source_table,
        )

    @staticmethod
    @retry(
        stop=stop_after_attempt(_CALLBACK_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception(_is_retryable_callback_error),
        before_sleep=_log_callback_retry,
        reraise=True,
    )
    def _execute_http_callback(callback_url: str, body: dict[str, Any]) -> None:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(callback_url, json=body)
            response.raise_for_status()

    def _build_callback_payload(self, status: str) -> dict[str, Any]:
        """JSON de respuesta para callbackUrl (n8n / Cloud Run)."""
        return {
            "status": status,
            "jobId": self._job_id,
            "schemaName": self.payload.schema_name,
            "sourceTable": self.payload.source_table,
            "targetTable": self.tables.boveda_table,
            "configTable": self.tables.config_table,
            "bovedaTable": self.tables.boveda_table,
            "identidadTable": self.tables.identidad_table,
            "callbackUrl": self.payload.callback_url,
            "summary": self._summary.to_callback_dict(),
        }

    def _send_callback(self, status: str) -> None:
        callback_url = self.payload.callback_url
        if not callback_url or not callback_url.startswith(("http://", "https://")):
            logger.info("Callback Catalyst omitido — URL no válida o vacía: %s", callback_url)
            return

        body = self._build_callback_payload(status)

        try:
            self._execute_http_callback(callback_url, body)
            logger.info(
                "Callback Catalyst enviado — url=%s, job_id=%s, status=%s",
                callback_url,
                self._job_id,
                status,
            )
        except Exception as exc:
            logger.error(
                "Fallo definitivo al enviar callback Catalyst a %s: %s",
                callback_url,
                exc,
            )

    def run(self, job_id: str | None = None) -> JobSummary:
        if not DB_URL:
            raise RuntimeError("DATABASE_URL no está configurada.")

        job_id = job_id or str(uuid4())
        self._job_id = job_id
        started_at = time.perf_counter()

        logger.info(
            "🧪 [CATALYST START] job_id=%s, schema=%s, source=%s, config=%s, boveda=%s, identidad=%s",
            job_id,
            self.payload.schema_name,
            self.payload.source_table,
            self.tables.config_table,
            self.tables.boveda_table,
            self.tables.identidad_table,
        )

        callback_status = "failed"
        try:
            config_rows = load_config_rows(
                self.payload.schema_name,
                self.payload.source_table,
                self.tables.config_table,
            )
            key_columns = [row for row in config_rows if row.es_llave]
            persist_columns = config_rows

            self._inspect_source_table()
            if self._has_req_aceptado:
                self._summary.filas_omitidas = self._count_rejected_rows()
                logger.info(
                    "🔒 [CATALYST] Filtro req_aceptado activo — filas omitidas=%d",
                    self._summary.filas_omitidas,
                )

            ensure_boveda_table(self.payload.schema_name, self.tables.boveda_table)
            ensure_identidad_table(self.payload.schema_name, self.tables.identidad_table)

            for chunk in self._iter_source_chunks():
                for record in chunk.to_dict(orient="records"):
                    self._summary.filas_procesadas += 1
                    try:
                        self._process_row(
                            record,
                            persist_columns,
                            key_columns,
                            job_id,
                        )
                    except Exception as exc:
                        message = (
                            f"Error procesando fila {self._summary.filas_procesadas}: {exc}"
                        )
                        logger.error("❌ [CATALYST] %s", message, exc_info=True)
                        self._summary.errores.append(message)

            finalize_entity_lifecycle(
                self.payload.schema_name,
                self.tables.identidad_table,
                self.tables.boveda_table,
                tabla_origen=self.payload.source_table,
                job_id=job_id,
                summary=self._summary,
            )

            callback_status = "success"
            elapsed = time.perf_counter() - started_at
            logger.info(
                "✅ [CATALYST DONE] %.2fs — job_id=%s, filas=%d, omitidas=%d, insertados=%d, "
                "actualizados=%d, sin_cambio=%d, eliminados=%d, inactivas=%d, errores=%d",
                elapsed,
                job_id,
                self._summary.filas_procesadas,
                self._summary.filas_omitidas,
                self._summary.registros_insertados,
                self._summary.registros_actualizados,
                self._summary.registros_sin_cambio,
                self._summary.registros_eliminados,
                self._summary.entidades_inactivadas,
                len(self._summary.errores),
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started_at
            self._summary.errores.append(str(exc))
            logger.error(
                "❌ [CATALYST ERROR] %.2fs — job_id=%s: %s",
                elapsed,
                job_id,
                exc,
                exc_info=True,
            )
        finally:
            self._send_callback(callback_status)

        return self._summary
