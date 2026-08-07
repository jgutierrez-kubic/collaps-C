"""Motor de materialización Capa 4: bóveda KV vigente → tabla ancha a_4_*."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import text
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.catalyst.config_reader import load_config_rows
from app.core.catalyst.materialize_sql import (
    build_materialize_ddl,
    build_pivot_select_sql,
)
from app.core.catalyst.models import MaterializeSummary
from app.core.catalyst.table_contract import qualified_table
from app.core.db import DB_URL, get_db_engine
from app.models.catalyst_payload import CatalystMaterializePayload

logger = logging.getLogger(__name__)

_CALLBACK_MAX_ATTEMPTS = 3


def _is_retryable_callback_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def _log_callback_retry(retry_state: object) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Reintento callback materialize — intento=%s, error=%s",
        retry_state.attempt_number,
        exc,
    )


class CatalystMaterializeEngine:
    """Materializa bóveda VIGENTE en tabla ancha a_4_* según configuración RMS."""

    def __init__(self, payload: CatalystMaterializePayload) -> None:
        self.payload = payload
        self.tables = payload.resolve_tables()
        self.target_table = payload.target_table
        self._job_id: str | None = None
        self._summary = MaterializeSummary()

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
        return {
            "status": status,
            "jobId": self._job_id,
            "schemaName": self.payload.schema_name,
            "sourceTable": self.payload.source_table,
            "targetTable": self.target_table,
            "configTable": self.tables.config_table,
            "bovedaTable": self.tables.boveda_table,
            "callbackUrl": self.payload.callback_url,
            "summary": self._summary.to_callback_dict(),
        }

    def _send_callback(self, status: str) -> None:
        callback_url = self.payload.callback_url
        if not callback_url or not callback_url.startswith(("http://", "https://")):
            logger.info(
                "Callback materialize omitido — URL no válida o vacía: %s",
                callback_url,
            )
            return

        body = self._build_callback_payload(status)
        try:
            self._execute_http_callback(callback_url, body)
            logger.info(
                "Callback materialize enviado — url=%s, job_id=%s, status=%s",
                callback_url,
                self._job_id,
                status,
            )
        except Exception as exc:
            logger.error(
                "Fallo definitivo al enviar callback materialize a %s: %s",
                callback_url,
                exc,
            )

    def run(self, job_id: str | None = None) -> MaterializeSummary:
        if not DB_URL:
            raise RuntimeError("DATABASE_URL no está configurada.")

        job_id = job_id or str(uuid4())
        self._job_id = job_id
        started_at = time.perf_counter()

        logger.info(
            "📦 [MATERIALIZE START] job_id=%s, schema=%s, source=%s, target=%s, "
            "config=%s, boveda=%s",
            job_id,
            self.payload.schema_name,
            self.payload.source_table,
            self.target_table,
            self.tables.config_table,
            self.tables.boveda_table,
        )

        callback_status = "failed"
        try:
            config_rows = load_config_rows(
                self.payload.schema_name,
                self.payload.source_table,
                self.tables.config_table,
            )
            property_columns = [
                row for row in config_rows if row.guardar and row.columna_origen
            ]
            self._summary.columnas_creadas = len(property_columns) + 5

            select_sql = build_pivot_select_sql(
                self.payload.schema_name,
                self.tables.boveda_table,
                source_table=self.payload.source_table,
                config_rows=config_rows,
            )
            drop_sql, create_sql = build_materialize_ddl(
                self.payload.schema_name,
                self.target_table,
                select_sql,
            )

            with get_db_engine().begin() as conn:
                conn.execute(text(drop_sql))
                conn.execute(text(create_sql))
                count_result = conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {qualified_table(self.payload.schema_name, self.target_table)}"
                    )
                )
                self._summary.entidades_materializadas = int(count_result.scalar() or 0)

            callback_status = "success"
            elapsed = time.perf_counter() - started_at
            logger.info(
                "✅ [MATERIALIZE DONE] %.2fs — job_id=%s, target=%s, entidades=%d, columnas=%d",
                elapsed,
                job_id,
                self.target_table,
                self._summary.entidades_materializadas,
                self._summary.columnas_creadas,
            )
        except Exception as exc:
            message = f"Error en materialización: {exc}"
            logger.error("❌ [MATERIALIZE] %s", message, exc_info=True)
            self._summary.errores.append(message)
            raise
        finally:
            self._send_callback(callback_status)

        return self._summary
