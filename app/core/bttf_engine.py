import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pandas as pd
from sqlalchemy import create_engine, text

from app.core.analysis_engine import AnalysisEngine
from app.core.config import DB_URL
from app.models.payload import DataSource, JobPayload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

_ESTADO_BTTF_MAP = {
    "left_only": "Only A",
    "right_only": "Only B",
    "both": "Match",
}

OUTPUT_DIR = Path("outputs")
OUTPUT_FILE = OUTPUT_DIR / "resultado_job.csv"


class CondenserEngine:
    def __init__(self, payload: JobPayload) -> None:
        self.payload = payload

    @staticmethod
    def _read_source(source: DataSource) -> pd.DataFrame:
        if source.type == "database":
            if not source.query:
                raise ValueError("El source type='database' requiere el campo 'query'.")

            if not DB_URL:
                raise RuntimeError(
                    "DATABASE_URL no está configurada. "
                    "Exporte la variable de entorno antes de usar fuentes database."
                )

            engine = create_engine(DB_URL)
            with engine.connect() as connection:
                return pd.read_sql(source.query, con=connection)

        if not source.path:
            raise ValueError("El source no define un campo 'path'.")

        extension = Path(source.path).suffix.lower()

        if extension == ".csv":
            return pd.read_csv(source.path)
        if extension in {".xlsx", ".xls"}:
            return pd.read_excel(source.path)

        raise ValueError(f"Extensión no soportada para lectura: {extension}")

    def _01_roads(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        roads = self.payload.module_01_roads

        logger.info("Iniciando descarga de fuentes (Roads)...")
        logger.info(
            "Fuente A — type=%s, path=%s, query=%s",
            roads.source_a.type,
            roads.source_a.path,
            roads.source_a.query,
        )
        df_a = self._read_source(roads.source_a)
        logger.info("Fuente A: %d filas obtenidas", len(df_a))

        logger.info(
            "Fuente B — type=%s, path=%s, query=%s",
            roads.source_b.type,
            roads.source_b.path,
            roads.source_b.query,
        )
        df_b = self._read_source(roads.source_b)
        logger.info("Fuente B: %d filas obtenidas", len(df_b))

        return df_a, df_b

    def _02_trains(self, df_a: pd.DataFrame, df_b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        if self.payload.module_02_trains.stack_required:
            # TODO: concatenar listas de fuentes apiladas cuando la ingesta real esté conectada.
            print("[Trains] stack_required=True — concatenación de listas pendiente de implementación.")
        return df_a, df_b

    @staticmethod
    def _coalesce_suffix_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Unifica pares de columnas _x/_y generados por el merge en una sola columna."""
        df_result = df.copy()
        x_columns = [col for col in df_result.columns if col.endswith("_x")]

        for col_x in x_columns:
            base_name = col_x[:-2]
            col_y = f"{base_name}_y"

            if col_y not in df_result.columns:
                continue

            df_result[base_name] = df_result[col_x].combine_first(df_result[col_y])
            df_result = df_result.drop(columns=[col_x, col_y])

        return df_result

    def _03_condenser(self, df_a: pd.DataFrame, df_b: pd.DataFrame) -> pd.DataFrame:
        join_keys = self.payload.module_03_condenser.join_keys

        df_condensado = pd.merge(
            df_a,
            df_b,
            on=join_keys,
            how="outer",
            indicator=True,
        )
        df_condensado = df_condensado.rename(columns={"_merge": "estado_bttf"})
        df_condensado["estado_bttf"] = df_condensado["estado_bttf"].map(_ESTADO_BTTF_MAP)
        return self._coalesce_suffix_columns(df_condensado)

    def _mr_fusion(self, df_condensado: pd.DataFrame) -> pd.DataFrame:
        df_result = df_condensado.copy()

        for rule in self.payload.module_mr_fusion.rules:
            try:
                df_result[rule.target_column] = df_result.eval(rule.expression)
            except Exception as exc:
                logger.error(
                    "Regla Mr. Fusion inválida — target=%s, expression=%s: %s",
                    rule.target_column,
                    rule.expression,
                    exc,
                )

        return df_result

    @staticmethod
    def _add_directus_primary_key(engine, schema_name: str, table_name: str) -> None:
        # IF NOT EXISTS evita fallo en la 2ª ejecución (modo append histórico).
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
                "No se pudo añadir id PRIMARY KEY en %s.%s (columna o constraint ya existe): %s",
                schema_name,
                table_name,
                exc,
            )

    @staticmethod
    def _stamp_run_metadata(
        df_result: pd.DataFrame,
        run_id: str,
        created_at: datetime,
    ) -> pd.DataFrame:
        df_stamped = df_result.copy()
        df_stamped["run_id"] = run_id
        df_stamped["created_at"] = created_at
        return df_stamped

    @staticmethod
    def _register_directus_collection(schema_name: str, table_name: str) -> None:
        AnalysisEngine._register_directus_collection(schema_name, table_name)

    def _persist_result(
        self,
        df_result: pd.DataFrame,
        run_id: str,
        created_at: datetime,
    ) -> tuple[str, str] | None:
        persistence = self.payload.module_00_on.persistence
        target_table = persistence.get("target_table")
        # Historial de ejecuciones: siempre append (nunca replace).
        if_exists = "append"

        df_to_write = self._stamp_run_metadata(df_result, run_id, created_at)

        if not DB_URL:
            logger.warning(
                "DATABASE_URL no configurada — fallback a exportación CSV local en %s",
                OUTPUT_FILE,
            )
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            df_to_write.to_csv(OUTPUT_FILE, index=False)
            logger.info("Resultado exportado en %s (run_id=%s)", OUTPUT_FILE, run_id)
            return None

        if not target_table:
            raise ValueError(
                "persistence.target_table es requerido cuando DATABASE_URL está configurada."
            )

        table_parts = target_table.split(".")
        if len(table_parts) != 2:
            raise ValueError(
                f"target_table inválido: '{target_table}'. Use el formato 'schema.tabla'."
            )

        schema_name, table_name = table_parts
        logger.info(
            "Guardando DataFrame final en PostgreSQL — destino=%s.%s, filas=%d, "
            "columnas=%d, if_exists=%s, run_id=%s",
            schema_name,
            table_name,
            len(df_to_write),
            len(df_to_write.columns),
            if_exists,
            run_id,
        )
        engine = create_engine(DB_URL)
        df_to_write.to_sql(
            name=table_name,
            schema=schema_name,
            con=engine,
            if_exists=if_exists,
            index=False,
        )
        logger.info(
            "Resultado persistido en PostgreSQL — %s.%s (if_exists=%s, run_id=%s)",
            schema_name,
            table_name,
            if_exists,
            run_id,
        )

        self._add_directus_primary_key(engine, schema_name, table_name)
        return schema_name, table_name

    def _send_webhook(self, job_id: str, status: str, message: str) -> None:
        notify_url = self.payload.module_00_on.notify_route

        if not notify_url.startswith(("http://", "https://")):
            logger.info("Webhook omitido — notify_route no es una URL HTTP: %s", notify_url)
            return

        payload = {
            "job_id": job_id,
            "status": status,
            "message": message,
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(notify_url, json=payload)
                response.raise_for_status()
            logger.info("Webhook enviado — job_id=%s, status=%s", job_id, status)
        except Exception as exc:
            logger.error("Fallo al enviar webhook a %s: %s", notify_url, exc)

    def run(self, job_id: str | None = None) -> pd.DataFrame | None:
        job_id = job_id or str(uuid4())
        run_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        job_status = "failed"
        message = "Proceso completado"
        result: pd.DataFrame | None = None

        logger.info(
            "Iniciando job BTTF — job_id=%s, run_id=%s, created_at=%s",
            job_id,
            run_id,
            created_at.isoformat(),
        )

        try:
            df_a, df_b = self._01_roads()
            df_a, df_b = self._02_trains(df_a, df_b)
            df_condensado = self._03_condenser(df_a, df_b)
            result = self._mr_fusion(df_condensado)

            persisted = self._persist_result(result, run_id=run_id, created_at=created_at)
            if persisted:
                schema_name, table_name = persisted
                self._register_directus_collection(schema_name, table_name)

            job_status = "success"
            logger.info(
                "Job BTTF completado exitosamente — job_id=%s, run_id=%s",
                job_id,
                run_id,
            )
        except Exception as e:
            message = f"Error en procesamiento: {e}"
            logging.error(f"Error crítico en el motor BTTF: {e}", exc_info=True)
        finally:
            self._send_webhook(job_id, job_status, message)

        return result
