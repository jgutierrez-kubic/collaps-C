import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import httpx
import pandas as pd
from sqlalchemy import inspect, text

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

OUTPUT_DIR = os.path.join("outputs", "analysis")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "resultado_analisis.csv")

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


class AnalysisEngine:
    def __init__(self, payload: AnalysisPayload) -> None:
        self.payload = payload
        self._last_summary: dict[str, Any] | None = None

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
    def _table_exists(engine, schema_name: str, table_name: str) -> bool:
        inspector = inspect(engine)
        return inspector.has_table(table_name, schema=schema_name)

    @staticmethod
    def _get_existing_columns(engine, schema_name: str, table_name: str) -> set[str]:
        inspector = inspect(engine)
        columns = inspector.get_columns(table_name, schema=schema_name)
        return {column["name"] for column in columns}

    def _auto_migrate_table(
        self,
        engine,
        schema_name: str,
        table_name: str,
        df: pd.DataFrame,
    ) -> None:
        if not self._table_exists(engine, schema_name, table_name):
            logger.info(
                "🛠️ [PYTHON - AUTO-MIGRATION] Tabla %s.%s no existe — se creará automáticamente.",
                schema_name,
                table_name,
            )
            return

        existing_columns = self._get_existing_columns(engine, schema_name, table_name)
        new_columns = [col for col in df.columns if col not in existing_columns]

        if not new_columns:
            logger.info(
                "🛠️ [PYTHON - AUTO-MIGRATION] Esquema alineado — sin columnas nuevas en %s.%s",
                schema_name,
                table_name,
            )
            return

        qualified_table = f"{self._quote_ident(schema_name)}.{self._quote_ident(table_name)}"

        with engine.begin() as conn:
            for column in new_columns:
                pg_type = self._pandas_dtype_to_pg(df[column])
                ddl = (
                    f"ALTER TABLE {qualified_table} "
                    f"ADD COLUMN IF NOT EXISTS {self._quote_ident(column)} {pg_type}"
                )
                logger.info(
                    "🛠️ [PYTHON - AUTO-MIGRATION] Agregando nueva columna '%s' (%s) a la tabla %s.%s...",
                    column,
                    pg_type,
                    schema_name,
                    table_name,
                )
                conn.execute(text(ddl))

    @staticmethod
    def _add_directus_primary_key(engine, schema_name: str, table_name: str) -> None:
        ddl = (
            f'ALTER TABLE "{schema_name}"."{table_name}" '
            "ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY"
        )
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info(
                "Columna id SERIAL PRIMARY KEY verificada/añadida — %s.%s",
                schema_name,
                table_name,
            )
        except Exception as exc:
            logger.warning(
                "No se pudo añadir id PRIMARY KEY en %s.%s: %s",
                schema_name,
                table_name,
                exc,
            )

    @staticmethod
    def _fetch_directus_credentials(
        engine,
        schema_name: str,
    ) -> tuple[str, str] | None:
        query = text(
            'SELECT directus_url, "Instance_Token" '
            'FROM public.portal_projects '
            'WHERE "Schema_Name" = :schema_name'
        )
        try:
            with engine.connect() as conn:
                row = conn.execute(query, {"schema_name": schema_name}).mappings().first()
        except Exception as exc:
            logger.warning(
                "Error consultando portal_projects para schema=%s: %s",
                schema_name,
                exc,
            )
            return None

        if row is None:
            logger.info(
                "Credenciales de Directus no encontradas en portal_projects para el esquema %s. "
                "Omitiendo auto-registro.",
                schema_name,
            )
            return None

        directus_url = str(row.get("directus_url") or "").strip()
        instance_token = str(row.get("Instance_Token") or "").strip()

        if not directus_url or not instance_token:
            logger.info(
                "Credenciales de Directus no encontradas en portal_projects para el esquema %s. "
                "Omitiendo auto-registro.",
                schema_name,
            )
            return None

        return directus_url, instance_token

    @staticmethod
    def _is_directus_collection_exists_error(exc: httpx.HTTPStatusError) -> bool:
        if exc.response.status_code != 400:
            return False

        try:
            payload = exc.response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list):
                for error in errors:
                    if not isinstance(error, dict):
                        continue
                    extensions = error.get("extensions")
                    if isinstance(extensions, dict) and extensions.get("code") == "INVALID_PAYLOAD":
                        return True
                    message = str(error.get("message", "")).lower()
                    if "already exists" in message:
                        return True

            if payload.get("code") == "INVALID_PAYLOAD":
                return True

        response_text = exc.response.text.lower()
        return "already exists" in response_text or "invalid_payload" in response_text

    @staticmethod
    def _register_directus_collection(schema_name: str, table_name: str) -> None:
        if not DB_URL:
            return

        engine = get_db_engine()
        credentials = AnalysisEngine._fetch_directus_credentials(engine, schema_name)
        if credentials is None:
            return

        directus_url, instance_token = credentials
        clean_collection_name = table_name.split(".")[-1]
        endpoint = f"{directus_url.rstrip('/')}/collections"
        headers = {"Authorization": f"Bearer {instance_token}"}
        body = {"collection": clean_collection_name}

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
            logger.info("Tabla registrada en Directus — collection=%s", clean_collection_name)
        except httpx.HTTPStatusError as exc:
            if AnalysisEngine._is_directus_collection_exists_error(exc):
                logger.info(
                    "La colección '%s' ya está registrada en Directus. Omitiendo creación.",
                    clean_collection_name,
                )
                return

            logger.warning(
                "Autoregistro Directus respondió HTTP %s para %s: %s",
                exc.response.status_code,
                clean_collection_name,
                exc.response.text,
            )
        except Exception as exc:
            logger.warning("Autoregistro Directus falló para %s: %s", clean_collection_name, exc)

    @staticmethod
    def _stamp_run_metadata(
        df_result: pd.DataFrame,
        run_id: str,
        created_at: datetime,
        payload: AnalysisPayload,
    ) -> pd.DataFrame:
        df_stamped = df_result.copy()
        df_stamped["run_id"] = run_id
        df_stamped["created_at"] = created_at
        if payload.analysis_id:
            df_stamped["analysis_id"] = payload.analysis_id
        if payload.nombre_analisis:
            df_stamped["nombre_analisis"] = payload.nombre_analisis
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
        tabla_a = self.payload.tabla_a
        tabla_b = self.payload.tabla_b
        llave_a = self.payload.llave_cruce_a
        llave_b = self.payload.llave_cruce_b

        stats_sql = text(
            f'SELECT '
            f'(SELECT COUNT(*) FROM "{schema_name}"."{tabla_a}") AS total_a, '
            f'(SELECT COUNT(DISTINCT "{llave_a}") FROM "{schema_name}"."{tabla_a}") AS unique_a, '
            f'(SELECT COUNT(*) FROM "{schema_name}"."{tabla_b}") AS total_b, '
            f'(SELECT COUNT(DISTINCT "{llave_b}") FROM "{schema_name}"."{tabla_b}") AS unique_b'
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
            "total_rows": total_rows,
            "matches": matches,
            "only_a": only_a,
            "only_b": only_b,
            "has_duplicates": has_duplicates,
        }

    def _apply_collaps_transformations(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aplica execute_transformation por cada par de columnas según metodos_calculo."""
        result = df.copy()
        columnas_a = split_csv(self.payload.columnas_a)
        columnas_b = split_csv(self.payload.columnas_b)
        metodos = split_csv(self.payload.metodos_calculo)

        logger.info(
            "⚙️ [PYTHON - COLLAPS] Aplicando %d transformación(es) vía collaps_engine...",
            len(metodos),
        )

        for col_a, col_b, method_raw in zip(columnas_a, columnas_b, metodos):
            method_id, swap_operands, _method_label = self._resolve_method(method_raw)
            col_a_key = f"{col_a}_a"
            col_b_key = f"{col_b}_b"

            if col_a_key not in result.columns or col_b_key not in result.columns:
                raise KeyError(
                    f"Columnas requeridas no encontradas en el resultado SQL: "
                    f"'{col_a_key}' / '{col_b_key}'"
                )

            result_values: list[object] = []
            match_values: list[object] = []

            for _, row in result.iterrows():
                val_a = row[col_a_key]
                val_b = row[col_b_key]
                if swap_operands:
                    val_a, val_b = val_b, val_a

                transformation = execute_transformation(val_a, val_b, method_id)
                if transformation["error"]:
                    logger.warning(
                        "⚠️ [PYTHON - COLLAPS] Error en %s para %s/%s: %s",
                        method_id,
                        col_a,
                        col_b,
                        transformation["error"],
                    )
                result_values.append(transformation["result_value"])
                match_values.append(transformation["is_match"])

            out_col = self._result_column_name(col_a, col_b, method_id)
            result[out_col] = result_values

            is_pure_boolean = method_id in _BOOLEAN_PURE_METHODS
            if not is_pure_boolean and any(value is not None for value in match_values):
                match_col = self._match_column_name(col_a, col_b, method_id)
                result[match_col] = match_values

            logger.info(
                "⚙️ [PYTHON - COLLAPS] Método '%s' aplicado — columna destino '%s'",
                method_id,
                out_col,
            )

        return result

    def _execute_analysis_query(self, sql: str) -> pd.DataFrame:
        if not DB_URL:
            raise RuntimeError(
                "DATABASE_URL no está configurada. "
                "Configure la variable de entorno antes de ejecutar análisis."
            )

        db_target = get_database_target(DB_URL)
        logger.info(
            "🔌 [PYTHON - LOG 2] Conectando a la base de datos... destino=%s",
            db_target,
        )

        engine = get_db_engine()
        with engine.connect() as connection:
            df = pd.read_sql(text(sql), con=connection)

        logger.info(
            "📊 [PYTHON - LOG 3] Consulta ejecutada exitosamente. Filas: %d",
            len(df),
        )
        return df

    def _persist_result(
        self,
        df_result: pd.DataFrame,
        run_id: str,
        created_at: datetime,
    ) -> tuple[str, str] | None:
        schema_name = self.payload.schema_name
        table_name = self.payload.tabla_destino
        df_to_write = self._stamp_run_metadata(df_result, run_id, created_at, self.payload)

        if not DB_URL:
            logger.warning(
                "DATABASE_URL no configurada — fallback a exportación CSV local en %s",
                OUTPUT_FILE,
            )
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            df_to_write.to_csv(OUTPUT_FILE, index=False)
            logger.info("Resultado exportado en %s (run_id=%s)", OUTPUT_FILE, run_id)
            return None

        logger.info(
            "💾 [PYTHON - LOG 4] Guardando resultados en %s.%s... filas=%d, run_id=%s",
            schema_name,
            table_name,
            len(df_to_write),
            run_id,
        )

        engine = get_db_engine()
        self._auto_migrate_table(engine, schema_name, table_name, df_to_write)

        df_to_write.to_sql(
            name=table_name,
            schema=schema_name,
            con=engine,
            if_exists="append",
            index=False,
        )
        logger.info(
            "Resultado persistido en PostgreSQL — %s.%s (if_exists=append, run_id=%s)",
            schema_name,
            table_name,
            run_id,
        )

        self._add_directus_primary_key(engine, schema_name, table_name)
        return schema_name, table_name

    def _send_callback(self, status: str) -> None:
        callback_url = self.payload.callback_url
        if not callback_url:
            return

        if not callback_url.startswith(("http://", "https://")):
            logger.info("Callback omitido — URL no válida: %s", callback_url)
            return

        body: dict[str, Any] = {
            "status": status,
            "analysis_id": self.payload.analysis_id,
            "schema": self.payload.schema_name,
        }
        if self._last_summary is not None:
            body["summary"] = self._last_summary

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(callback_url, json=body)
                response.raise_for_status()
            logger.info(
                "Callback enviado — url=%s, analysis_id=%s, status=%s",
                callback_url,
                self.payload.analysis_id,
                status,
            )
        except Exception as exc:
            logger.error("Fallo al enviar callback a %s: %s", callback_url, exc)

    def run(self, job_id: str | None = None) -> pd.DataFrame | None:
        job_id = job_id or str(uuid4())
        run_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        result: pd.DataFrame | None = None
        started_at = time.perf_counter()

        logger.info(
            "🐍 [PYTHON - JOB START] Iniciando job_id=%s, run_id=%s, analysis_id=%s, source=%s",
            job_id,
            run_id,
            self.payload.analysis_id,
            self.payload.source,
        )

        try:
            sql = build_analysis_sql(self.payload)
            logger.info("📐 [PYTHON - LOG 1] Query SQL generada:\n%s", sql)

            source_stats = self._fetch_source_uniqueness_stats()
            log_join_uniqueness_warning(self.payload, source_stats)

            result = self._execute_analysis_query(sql)
            self._last_summary = self._build_analytical_summary(result, source_stats)

            if self._last_summary["has_duplicates"]:
                logger.warning(
                    "⚠️ [PYTHON - DUPLICADOS] El JOIN generó %d filas; posible producto cartesiano "
                    "(unique_a=%s, unique_b=%s). Revise unicidad de llaves de cruce.",
                    self._last_summary["total_rows"],
                    source_stats["unique_a"] if source_stats else "N/A",
                    source_stats["unique_b"] if source_stats else "N/A",
                )

            result = self._apply_collaps_transformations(result)
            persisted = self._persist_result(result, run_id=run_id, created_at=created_at)

            if persisted:
                schema_name, table_name = persisted
                self._register_directus_collection(schema_name, table_name)

            self._send_callback("success")

            elapsed = time.perf_counter() - started_at
            logger.info(
                "✅ [PYTHON - LOG 5] Proceso completado exitosamente en %.2f segundos — "
                "job_id=%s, run_id=%s",
                elapsed,
                job_id,
                run_id,
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
