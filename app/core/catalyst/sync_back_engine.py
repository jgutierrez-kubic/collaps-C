"""Motor Sync-Back: edición manual de propiedades en bóveda SCD2."""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import text

from app.core.catalyst.boveda_writer import (
    compute_firma_auditoria,
    ensure_boveda_table,
    upsert_boveda_record,
)
from app.core.catalyst.cleanup import to_valor_limpio, to_valor_original
from app.core.catalyst.config_reader import load_config_rows
from app.core.catalyst.identity_writer import (
    ensure_identidad_table,
    refresh_total_propiedades_activas,
)
from app.core.catalyst.models import SyncBackSummary
from app.core.catalyst.origen_dato import ORIGEN_EDICION_MANUAL
from app.core.catalyst.table_contract import qualified_table
from app.core.db import DB_URL, get_db_engine
from app.models.catalyst_payload import CatalystSyncBackPayload

logger = logging.getLogger(__name__)


def _lookup_identity(
    schema_name: str,
    identidad_table: str,
    *,
    entidad_interna_id: str,
    tabla_origen: str,
) -> str:
    qualified = qualified_table(schema_name, identidad_table)
    sql = text(
        f"SELECT llave_humana_completa FROM {qualified} "
        "WHERE entidad_interna_id = :entidad_interna_id "
        "AND tabla_origen = :tabla_origen "
        "LIMIT 1"
    )
    with get_db_engine().connect() as conn:
        row = conn.execute(
            sql,
            {
                "entidad_interna_id": entidad_interna_id,
                "tabla_origen": tabla_origen,
            },
        ).mappings().first()

    if not row or not str(row["llave_humana_completa"]).strip():
        raise ValueError(
            f"Entidad no encontrada en identidad: {entidad_interna_id} "
            f"(tabla_origen={tabla_origen})."
        )
    return str(row["llave_humana_completa"])


class CatalystSyncBackEngine:
    """Aplica edición manual en bóveda con origen edicion_manual y SCD2."""

    def __init__(self, payload: CatalystSyncBackPayload) -> None:
        self.payload = payload
        self.tables = payload.resolve_tables()
        self._summary = SyncBackSummary()

    def run(self) -> SyncBackSummary:
        if not DB_URL:
            raise RuntimeError("DATABASE_URL no está configurada.")

        job_id = str(uuid4())
        propiedad = self.payload.propiedad
        entidad_id = self.payload.entidad_interna_id

        logger.info(
            "🔄 [SYNC-BACK] entidad=%s, propiedad=%s, usuario=%s, source=%s",
            entidad_id,
            propiedad,
            self.payload.usuario_email,
            self.payload.source_table,
        )

        config_rows = load_config_rows(
            self.payload.schema_name,
            self.payload.source_table,
            self.tables.config_table,
        )
        config_match = next(
            (row for row in config_rows if row.columna_origen == propiedad),
            None,
        )
        if config_match is None:
            raise ValueError(
                f"Propiedad '{propiedad}' no configurada para "
                f"source_table='{self.payload.source_table}'."
            )
        if not config_match.guardar:
            raise ValueError(f"Propiedad '{propiedad}' no está marcada como guardar.")

        ensure_boveda_table(self.payload.schema_name, self.tables.boveda_table)
        ensure_identidad_table(self.payload.schema_name, self.tables.identidad_table)

        llave_humana = _lookup_identity(
            self.payload.schema_name,
            self.tables.identidad_table,
            entidad_interna_id=entidad_id,
            tabla_origen=self.payload.source_table,
        )

        valor_original = to_valor_original(self.payload.nuevo_valor)
        valor_limpio = to_valor_limpio(
            self.payload.nuevo_valor,
            config_match.tipo_dato_generico,
        )
        firma = compute_firma_auditoria(valor_limpio)

        boveda_summary = self._summary.to_boveda_summary()
        upsert_boveda_record(
            self.payload.schema_name,
            self.tables.boveda_table,
            entidad_interna_id=entidad_id,
            llave_humana_completa=llave_humana,
            propiedad_origen=propiedad,
            valor_original=valor_original,
            valor_limpio=valor_limpio,
            firma_auditoria=firma,
            tipo_dato_generico=config_match.tipo_dato_generico,
            tabla_origen=self.payload.source_table,
            job_id=job_id,
            summary=boveda_summary,
            origen_dato=ORIGEN_EDICION_MANUAL,
            creado_por=self.payload.usuario_email,
        )
        self._summary.apply_boveda_summary(boveda_summary)

        refresh_total_propiedades_activas(
            self.payload.schema_name,
            self.tables.identidad_table,
            self.tables.boveda_table,
            entidad_interna_id=entidad_id,
            tabla_origen=self.payload.source_table,
        )

        logger.info(
            "✅ [SYNC-BACK DONE] entidad=%s, propiedad=%s, insertado=%d, "
            "actualizado=%d, sin_cambio=%d",
            entidad_id,
            propiedad,
            self._summary.registros_insertados,
            self._summary.registros_actualizados,
            self._summary.registros_sin_cambio,
        )
        return self._summary
