import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Literal
from uuid import uuid4
import httpx
import pandas as pd
from sqlalchemy import inspect, text
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import DB_URL
from app.core.db import get_database_target, get_db_engine
from app.core.query_builder import build_analysis_sql, log_join_uniqueness_warning, split_csv
from app.models.payload import AnalysisPayload
from collaps_engine.comparison_engine import OPERATIONS_REGISTRY
from collaps_engine.transformer import execute_transformation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

_CALLBACK_MAX_ATTEMPTS = 5
_RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 408, 500, 502, 503, 504})


def _is_retryable_callback_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_HTTP_STATUS_CODES
    return isinstance(exc, httpx.RequestError)


def _log_callback_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Reintentando callback HTTP — intento %d/%d, espera %.1fs, error: %s",
        retry_state.attempt_number,
        _CALLBACK_MAX_ATTEMPTS,
        retry_state.upcoming_sleep or 0,
        exc,
    )

OUTPUT_DIR = os.path.join("outputs", "analysis")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "resultado_analisis.csv")
SQL_CHUNK_SIZE = int(os.getenv("SQL_CHUNK_SIZE", "10000"))

# Alias legacy COLLAPS → collaps_engine (swap=True invierte operandos a val_b - val_a)
_LEGACY_METHOD_MAP: dict[str, tuple[str, bool]] = {
    "DIFERENCIA": ("math_sub", True),
    "IGUALDAD": ("strict_equal", False),
}

# Métodos cuyo result_value ya es booleano — no se anexa columna is_match adicional.
_BOOLEAN_PURE_METHODS: frozenset[str] = frozenset({
    "strict_equal",
    "normalized_equal",
    "date_equal",
    "regex_match",
    "null_check",
    "boolean_logic",
    "contains_check",
})

# Columnas de metadatos/rastreo que se agrupan al final antes de persistir.
_METADATA_COLUMNS: tuple[str, ...] = (
    "run_id",
    "created_at",
    "timestamp",
    "job_id",
    "estado_cruce",
    "analysis_id",
    "analysis_name",
    "source",
)


class AnalysisEngine:
    def __init__(self, payload: AnalysisPayload) -> None:
        self.payload = payload
        self._last_summary: dict[str, Any] | None = None
        self._job_id: str | None = None
        self.update_schema = False
        self._filas_insertadas = 0

    @staticmethod
    def _sanitize_column_part(part: str) -> str:
        """Sanitiza una parte individual de nombre de columna SQL."""
        clean = str(part).strip().lower().replace("/", "_").replace(" ", "_")
        clean = re.sub(r"[^a-z0-9_]", "_", clean)
        clean = re.sub(r"_+", "_", clean).strip("_")
        if not clean:
            raise ValueError(f"No se pudo sanitizar la parte de columna: '{part}'")
        return clean

    @staticmethod
    def _columns_are_equivalent(col_a: str, col_b: str) -> bool:
        return (
            AnalysisEngine._sanitize_column_part(col_a)
            == AnalysisEngine._sanitize_column_part(col_b)
        )

    @staticmethod
    def _indexed_column_name(index: int, suffix: str) -> str:
        """Genera nombre indexado: '{index}_{suffix_sanitizado}'."""
        clean_suffix = AnalysisEngine._sanitize_column_part(suffix)
        return f"{index}_{clean_suffix}"

    @staticmethod
    def _indexed_source_column_name(index: int, col_name: str, side: str) -> str:
        """Ej: index=0, col='nombre', side='A' → '0_nombreA'."""
        san = AnalysisEngine._sanitize_column_part(col_name)
        return f"{index}_{san}{side.upper()}"

    @staticmethod
    def _result_suffix(method_raw: str, method_id: str) -> str:
        legacy_key = method_raw.strip().upper()
        if legacy_key in _LEGACY_METHOD_MAP:
            return AnalysisEngine._sanitize_column_part(legacy_key.lower())
        return AnalysisEngine._sanitize_column_part(method_id)

    @staticmethod
    def _reorder_columns_for_persist(df: pd.DataFrame) -> pd.DataFrame:
        """Agrupa columnas de metadatos/rastreo al final del DataFrame."""
        metadata = [col for col in _METADATA_COLUMNS if col in df.columns]
        data_columns = [col for col in df.columns if col not in metadata]
        return df[data_columns + metadata]

    @staticmethod
    def _allocate_next_run_id(conn_or_engine, schema_name: str, table_name: str) -> int:
        """Obtiene el siguiente run_id incremental para la tabla destino."""
        if not AnalysisEngine._table_exists(conn_or_engine, schema_name, table_name):
            return 1

        sql = text(
            f'SELECT MAX(run_id) AS max_run_id FROM "{schema_name}"."{table_name}"'
        )
        try:
            if hasattr(conn_or_engine, "connect"):
                with conn_or_engine.connect() as conn:
                    row = conn.execute(sql).mappings().first()
            else:
                row = conn_or_engine.execute(sql).mappings().first()
        except Exception as exc:
            logger.info(
                "No se pudo leer MAX(run_id) en %s.%s (%s) — iniciando en 1.",
                schema_name,
                table_name,
                exc,
            )
            return 1

        max_run_id = row["max_run_id"] if row else None
        if max_run_id is None:
            return 1
        return int(max_run_id) + 1

    @staticmethod
    def _result_column_name(col_a: str, col_b: str, method_id: str) -> str:
        san_a = AnalysisEngine._sanitize_column_part(col_a)
        san_b = AnalysisEngine._sanitize_column_part(col_b)
        san_method = AnalysisEngine._sanitize_column_part(method_id)
        if AnalysisEngine._columns_are_equivalent(col_a, col_b):
            return f"{san_a}__{san_method}"
        return f"{san_a}__vs__{san_b}__{san_method}"

    @staticmethod
    def _match_column_name(col_a: str, col_b: str, method_id: str) -> str:
        return f"is_match__{AnalysisEngine._result_column_name(col_a, col_b, method_id)}"

    @staticmethod
    def _quote_ident(name: str) -> str:
        return f'"{name}"'

    @staticmethod
    def _pandas_dtype_to_pg(series: pd.Series) -> str:
        dtype = series.dtype
        if pd.api.types.is_integer_dtype(dtype):
            return "BIGINT"
        if pd.api.types.is_float_dtype(dtype):
            return "DOUBLE PRECISION"
        if pd.api.types.is_bool_dtype(dtype):
            return "BOOLEAN"
        if pd.api.types.is_datetime64_any_dtype(dtype):
            return "TIMESTAMPTZ"
        return "TEXT"

    @staticmethod
    def _table_exists(conn_or_engine, schema_name: str, table_name: str) -> bool:
        inspector = inspect(conn_or_engine)
        return inspector.has_table(table_name, schema=schema_name)

    @staticmethod
    def _get_existing_columns(conn_or_engine, schema_name: str, table_name: str) -> set[str]:
        inspector = inspect(conn_or_engine)
        columns = inspector.get_columns(table_name, schema=schema_name)
        return {column["name"] for column in columns}

    def _auto_migrate_table(
        self,
        conn,
        schema_name: str,
        table_name: str,
        df: pd.DataFrame,
    ) -> bool:
        """Ejecuta ALTER TABLE ADD COLUMN para columnas nuevas. Retorna True si hubo cambios."""
        if not self._table_exists(conn, schema_name, table_name):
            return False

        existing_columns = self._get_existing_columns(conn, schema_name, table_name)
        new_columns = [col for col in df.columns if col not in existing_columns]
        if not new_columns:
            return False

        qualified_table = f"{self._quote_ident(schema_name)}.{self._quote_ident(table_name)}"

        for column in new_columns:
            pg_type = self._pandas_dtype_to_pg(df[column])
            ddl = (
                f"ALTER TABLE {qualified_table} "
                f"ADD COLUMN IF NOT EXISTS {self._quote_ident(column)} {pg_type}"
            )
            conn.execute(text(ddl))

        logger.info(
            "🛠️ [AUTO-MIGRATION] %d columna(s) nueva(s) en %s.%s",
            len(new_columns),
            schema_name,
            table_name,
        )
        return True

    @staticmethod
    def _stamp_run_metadata(
        df_result: pd.DataFrame,
        run_id: int,
        created_at: datetime,
        payload: AnalysisPayload,
        job_id: str | None = None,
    ) -> pd.DataFrame:
        df_stamped = df_result.copy()
        df_stamped["run_id"] = run_id
        df_stamped["created_at"] = created_at
        df_stamped["timestamp"] = created_at
        if job_id:
            df_stamped["job_id"] = job_id
        if payload.analysis_id:
            df_stamped["analysis_id"] = payload.analysis_id
        if payload.analysis_name:
            df_stamped["analysis_name"] = payload.analysis_name
        df_stamped["source"] = payload.source
        return df_stamped

    @staticmethod
    def _resolve_method(method_raw: str) -> tuple[str, bool, str]:
        """Resuelve method_id del registry y si se deben invertir operandos."""
        legacy_key = method_raw.strip().upper()
        if legacy_key in _LEGACY_METHOD_MAP:
            method_id, swap = _LEGACY_METHOD_MAP[legacy_key]
            return method_id, swap, legacy_key

        method_id = method_raw.strip().lower()
        if method_id not in OPERATIONS_REGISTRY:
            raise ValueError(f"Método no registrado en collaps_engine: '{method_raw}'")
        return method_id, False, method_id

    def _fetch_source_uniqueness_stats(self) -> dict[str, int] | None:
        if not DB_URL:
            return None

        schema_name = self.payload.schema_name
        table_a = self.payload.table_a
        table_b = self.payload.table_b
        llave_a = self.payload.join_key_a
        llave_b = self.payload.join_key_b

        stats_sql = text(
            f'SELECT '
            f'(SELECT COUNT(*) FROM "{schema_name}"."{table_a}") AS total_a, '
            f'(SELECT COUNT(DISTINCT "{llave_a}") FROM "{schema_name}"."{table_a}") AS unique_a, '
            f'(SELECT COUNT(*) FROM "{schema_name}"."{table_b}") AS total_b, '
            f'(SELECT COUNT(DISTINCT "{llave_b}") FROM "{schema_name}"."{table_b}") AS unique_b'
        )

        engine = get_db_engine()
        with engine.connect() as connection:
            row = connection.execute(stats_sql).mappings().one()

        return {
            "total_a": int(row["total_a"]),
            "unique_a": int(row["unique_a"]),
            "total_b": int(row["total_b"]),
            "unique_b": int(row["unique_b"]),
        }

    @staticmethod
    def _build_analytical_summary(
        df: pd.DataFrame,
        source_stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        total_rows = len(df)
        matches = only_a = only_b = 0

        if "estado_cruce" in df.columns:
            counts = df["estado_cruce"].value_counts()
            matches = int(counts.get("Match", 0))
            only_a = int(counts.get("Only A", 0))
            only_b = int(counts.get("Only B", 0))

        has_duplicates = False
        if source_stats:
            total_a = source_stats["total_a"]
            unique_a = source_stats["unique_a"]
            total_b = source_stats["total_b"]
            unique_b = source_stats["unique_b"]
            has_duplicates = (
                total_a > unique_a
                or total_b > unique_b
                or total_rows > (unique_a + unique_b)
            )

        return {
            "totalRows": total_rows,
            "matches": matches,
            "onlyA": only_a,
            "onlyB": only_b,
            "hasDuplicates": has_duplicates,
        }

    @staticmethod
    def _init_analytical_summary() -> dict[str, Any]:
        return {
            "totalRows": 0,
            "matches": 0,
            "onlyA": 0,
            "onlyB": 0,
            "hasDuplicates": False,
        }

    @staticmethod
    def _merge_chunk_into_summary(
        summary: dict[str, Any],
        chunk: pd.DataFrame,
    ) -> dict[str, Any]:
        summary["totalRows"] += len(chunk)
        if "estado_cruce" not in chunk.columns:
            return summary

        counts = chunk["estado_cruce"].value_counts()
        summary["matches"] += int(counts.get("Match", 0))
        summary["onlyA"] += int(counts.get("Only A", 0))
        summary["onlyB"] += int(counts.get("Only B", 0))
        return summary

    @staticmethod
    def _finalize_analytical_summary(
        summary: dict[str, Any],
        source_stats: dict[str, int] | None,
    ) -> dict[str, Any]:
        if not source_stats:
            return summary

        total_a = source_stats["total_a"]
        unique_a = source_stats["unique_a"]
        total_b = source_stats["total_b"]
        unique_b = source_stats["unique_b"]
        total_rows = summary["totalRows"]
        summary["hasDuplicates"] = (
            total_a > unique_a
            or total_b > unique_b
            or total_rows > (unique_a + unique_b)
        )
        return summary

    @staticmethod
    def _apply_transformation_row(
        row: pd.Series,
        col_a_key: str,
        col_b_key: str,
        method_id: str,
        swap_operands: bool,
    ) -> dict[str, Any]:
        val_a = row[col_a_key]
        val_b = row[col_b_key]
        if swap_operands:
            val_a, val_b = val_b, val_a
        return execute_transformation(val_a, val_b, method_id)

    def _apply_collaps_transformations(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply execute_transformation per column pair according to calculation_methods."""
        result = df.copy()
        columnas_a = split_csv(self.payload.columns_a)
        columnas_b = split_csv(self.payload.columns_b)
        metodos = split_csv(self.payload.calculation_methods)

        logger.info(
            "⚙️ [PYTHON - COLLAPS] Aplicando %d transformación(es) vía collaps_engine...",
            len(metodos),
        )

        for pair_index, (col_a, col_b, method_raw) in enumerate(
            zip(columnas_a, columnas_b, metodos)
        ):
            method_id, swap_operands, _method_label = self._resolve_method(method_raw)
            col_a_key = f"{col_a}_a"
            col_b_key = f"{col_b}_b"

            if col_a_key not in result.columns or col_b_key not in result.columns:
                raise KeyError(
                    f"Columnas requeridas no encontradas en el resultado SQL: "
                    f"'{col_a_key}' / '{col_b_key}'"
                )

            transformations = result.apply(
                self._apply_transformation_row,
                axis=1,
                col_a_key=col_a_key,
                col_b_key=col_b_key,
                method_id=method_id,
                swap_operands=swap_operands,
            )

            for error_msg in transformations.map(lambda item: item.get("error")).dropna():
                logger.warning(
                    "⚠️ [PYTHON - COLLAPS] Error en %s para %s/%s: %s",
                    method_id,
                    col_a,
                    col_b,
                    error_msg,
                )

            result_values = transformations.map(lambda item: item["result_value"])
            match_values = transformations.map(lambda item: item["is_match"])

            indexed_a = self._indexed_source_column_name(pair_index, col_a, "A")
            indexed_b = self._indexed_source_column_name(pair_index, col_b, "B")
            indexed_method = self._indexed_column_name(pair_index, "metodo_aplicado")
            out_col = self._indexed_column_name(
                pair_index,
                self._result_suffix(method_raw, method_id),
            )

            result[indexed_a] = result[col_a_key]
            result[indexed_b] = result[col_b_key]
            result[indexed_method] = method_raw.strip()
            result[out_col] = result_values

            is_pure_boolean = method_id in _BOOLEAN_PURE_METHODS
            if not is_pure_boolean and match_values.notna().any():
                match_col = self._indexed_column_name(pair_index, "is_match")
                result[match_col] = match_values

            logger.info(
                "⚙️ [PYTHON - COLLAPS] Par %d — método '%s' → columnas '%s', '%s', '%s'",
                pair_index,
                method_id,
                indexed_a,
                indexed_b,
                out_col,
            )

        sql_source_columns = {
            f"{col}_a" for col in columnas_a
        } | {
            f"{col}_b" for col in columnas_b
        }
        drop_columns = [col for col in sql_source_columns if col in result.columns]
        if drop_columns:
            result = result.drop(columns=drop_columns)

        return result

    def _iter_analysis_chunks(
        self,
        sql: str,
        chunksize: int = SQL_CHUNK_SIZE,
    ) -> Iterator[pd.DataFrame]:
        """Lee el SQL de análisis en chunks; cada chunk usa una conexión corta y aislada."""
        if not DB_URL:
            raise RuntimeError(
                "DATABASE_URL no está configurada. "
                "Configure la variable de entorno antes de ejecutar análisis."
            )

        db_target = get_database_target(DB_URL)
        logger.info(
            "🔌 [PYTHON - LOG 2] Conectando a la base de datos... destino=%s, chunksize=%d",
            db_target,
            chunksize,
        )

        engine = get_db_engine()
        offset = 0
        chunk_index = 0

        while True:
            chunk_sql = text(
                f"SELECT * FROM ({sql}) AS _analysis_chunk "
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

            chunk_index += 1
            logger.info(
                "📊 [PYTHON - LOG 3] Chunk %d cargado — filas=%d",
                chunk_index,
                len(chunk),
            )
            yield chunk

            if len(chunk) < chunksize:
                break
            offset += chunksize

    def _persist_chunk(
        self,
        df_result: pd.DataFrame,
        *,
        run_id: int,
        created_at: datetime,
        job_id: str | None,
        if_exists: Literal["append", "replace"],
        migrate: bool,
    ) -> tuple[str, str] | None:
        schema_name = self.payload.schema_name
        table_name = self.payload.target_table
        df_to_write = self._stamp_run_metadata(
            df_result, run_id, created_at, self.payload, job_id=job_id
        )
        df_to_write = self._reorder_columns_for_persist(df_to_write)

        if not DB_URL:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            write_header = if_exists == "replace" or not os.path.exists(OUTPUT_FILE)
            df_to_write.to_csv(
                OUTPUT_FILE,
                mode="w" if if_exists == "replace" else "a",
                header=write_header,
                index=False,
            )
            if if_exists == "replace":
                self.update_schema = True
            self._filas_insertadas += len(df_to_write)
            logger.info(
                "Resultado exportado en %s (run_id=%d, mode=%s)",
                OUTPUT_FILE,
                run_id,
                if_exists,
            )
            return None

        engine = get_db_engine()

        if if_exists == "replace":
            self.update_schema = True

        logger.info(
            "💾 [PYTHON - LOG 4] Guardando chunk en %s.%s... filas=%d, run_id=%d, if_exists=%s",
            schema_name,
            table_name,
            len(df_to_write),
            run_id,
            if_exists,
        )

        with engine.begin() as conn:
            if migrate and self._auto_migrate_table(conn, schema_name, table_name, df_to_write):
                self.update_schema = True

            df_to_write.to_sql(
                name=table_name,
                schema=schema_name,
                con=conn,
                if_exists=if_exists,
                index=False,
            )

        self._filas_insertadas += len(df_to_write)
        return schema_name, table_name

    def _process_analysis_in_chunks(
        self,
        sql: str,
        source_stats: dict[str, int] | None,
        created_at: datetime,
        job_id: str,
    ) -> tuple[pd.DataFrame | None, tuple[str, str] | None]:
        summary = self._init_analytical_summary()
        last_chunk: pd.DataFrame | None = None
        persisted: tuple[str, str] | None = None
        run_id: int | None = None
        table_preexisted: bool | None = None

        for chunk_index, chunk in enumerate(self._iter_analysis_chunks(sql), start=1):
            summary = self._merge_chunk_into_summary(summary, chunk)
            transformed = self._apply_collaps_transformations(chunk)

            if DB_URL:
                if run_id is None:
                    engine = get_db_engine()
                    with engine.connect() as conn:
                        run_id = self._allocate_next_run_id(
                            conn,
                            self.payload.schema_name,
                            self.payload.target_table,
                        )
                        table_preexisted = self._table_exists(
                            conn,
                            self.payload.schema_name,
                            self.payload.target_table,
                        )

                if chunk_index == 1 and not table_preexisted:
                    if_exists: Literal["append", "replace"] = "replace"
                else:
                    if_exists = "append"
                migrate = chunk_index == 1
            else:
                if run_id is None:
                    run_id = 1
                if_exists = "replace" if chunk_index == 1 else "append"
                migrate = False

            persisted = self._persist_chunk(
                transformed,
                run_id=run_id,
                created_at=created_at,
                job_id=job_id,
                if_exists=if_exists,
                migrate=migrate,
            )
            last_chunk = transformed

        self._last_summary = self._finalize_analytical_summary(summary, source_stats)
        return last_chunk, persisted

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

    def _send_callback(self, status: str) -> None:
        callback_url = self.payload.callback_url
        if not callback_url:
            return

        if not callback_url.startswith(("http://", "https://")):
            logger.info("Callback omitido — URL no válida: %s", callback_url)
            return

        body: dict[str, Any] = {
            "status": status,
            "analysisId": self.payload.analysis_id,
            "schema": self.payload.schema_name,
            "targetTable": self.payload.target_table,
            "updateSchema": self.update_schema,
            "filas_insertadas": self._filas_insertadas,
        }
        if self._job_id:
            body["jobId"] = self._job_id
        if self._last_summary is not None:
            body["summary"] = self._last_summary

        try:
            self._execute_http_callback(callback_url, body)
            logger.info(
                "Callback enviado — url=%s, analysis_id=%s, status=%s",
                callback_url,
                self.payload.analysis_id,
                status,
            )
        except Exception as exc:
            logger.error(
                "Fallo definitivo al enviar callback a %s tras %d intentos: %s",
                callback_url,
                _CALLBACK_MAX_ATTEMPTS,
                exc,
            )

    def run(self, job_id: str | None = None) -> pd.DataFrame | None:
        job_id = job_id or str(uuid4())
        self._job_id = job_id
        self.update_schema = False
        self._filas_insertadas = 0
        created_at = datetime.now(timezone.utc)
        result: pd.DataFrame | None = None
        started_at = time.perf_counter()

        logger.info(
            "🐍 [PYTHON - JOB START] Iniciando job_id=%s, analysis_id=%s, source=%s",
            job_id,
            self.payload.analysis_id,
            self.payload.source,
        )

        try:
            sql = build_analysis_sql(self.payload)
            logger.info("📐 [PYTHON - LOG 1] Query SQL generada:\n%s", sql)

            source_stats = self._fetch_source_uniqueness_stats()
            log_join_uniqueness_warning(self.payload, source_stats)

            result, _persisted = self._process_analysis_in_chunks(
                sql,
                source_stats,
                created_at=created_at,
                job_id=job_id,
            )

            if self._last_summary and self._last_summary["hasDuplicates"]:
                logger.warning(
                    "⚠️ [PYTHON - DUPLICADOS] El JOIN generó %d filas; posible producto cartesiano "
                    "(unique_a=%s, unique_b=%s). Revise unicidad de llaves de cruce.",
                    self._last_summary["totalRows"],
                    source_stats["unique_a"] if source_stats else "N/A",
                    source_stats["unique_b"] if source_stats else "N/A",
                )

            self._send_callback("success")

            elapsed = time.perf_counter() - started_at
            logger.info(
                "✅ [PYTHON - LOG 5] Proceso completado exitosamente en %.2f segundos — job_id=%s",
                elapsed,
                job_id,
            )
        except Exception as e:
            elapsed = time.perf_counter() - started_at
            logger.error(
                "❌ [PYTHON - ERROR] Error detectado tras %.2f segundos: %s",
                elapsed,
                e,
                exc_info=True,
            )
            self._send_callback("failed")

        return result
