"""Tests del módulo Catalyst (RMS Genérico v1.4)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.catalyst.catalyst_engine import CatalystEngine
from app.core.catalyst.boveda_states import (
    ESTADO_ELIMINADO,
    ESTADO_HISTORICO,
    ESTADO_VIGENTE,
)
from app.core.catalyst.entity_states import ESTADO_ENTIDAD_ACTIVO, ESTADO_ENTIDAD_INACTIVO
from app.core.catalyst.boveda_writer import compute_firma_auditoria
from app.core.catalyst.cleanup import (
    is_empty_source_value,
    normalize_numeric_string,
    normalize_text_string,
    to_valor_limpio,
    to_valor_original,
)
from app.core.catalyst.governance import (
    is_req_aceptado,
    row_passes_acceptance_filter,
    source_table_has_column,
)
from app.core.catalyst.identity import (
    build_entidad_interna_id,
    build_llave_humana_completa,
    resolve_row_identity,
)
from app.core.catalyst.origen_dato import (
    ORIGEN_CARGA_MASIVA,
    ORIGEN_EDICION_MANUAL,
    ORIGEN_INFERENCIA_SISTEMA,
    ORIGENES_DATO,
)
from app.core.catalyst.table_contract import CatalystTableContract, qualified_table
from app.core.catalyst.materialize_sql import (
    build_materialize_ddl,
    build_pivot_select_sql,
    derive_materialize_table_name,
    _dedupe_property_columns,
)
from app.core.catalyst.sql_types import tipo_dato_to_pg_type
from app.core.catalyst.models import ConfigRow
from app.models.catalyst_payload import (
    CatalystJobPayload,
    CatalystMaterializePayload,
    CatalystSyncBackPayload,
)

TABLE_FIELDS = {
    "configTable": "a_2_config_ingesta_a",
    "bovedaTable": "a_3_boveda_kv",
    "identidadTable": "a_3_identidad",
}


def test_origen_dato_constants() -> None:
    assert ORIGEN_CARGA_MASIVA == "carga_masiva"
    assert ORIGEN_EDICION_MANUAL == "edicion_manual"
    assert ORIGEN_INFERENCIA_SISTEMA == "inferencia_sistema"
    assert ORIGENES_DATO == {
        "carga_masiva",
        "edicion_manual",
        "inferencia_sistema",
    }


def test_entity_estado_constants() -> None:
    assert ESTADO_ENTIDAD_ACTIVO == "ACTIVO"
    assert ESTADO_ENTIDAD_INACTIVO == "INACTIVO"


def test_boveda_estado_constants() -> None:
    assert ESTADO_VIGENTE == "VIGENTE"
    assert ESTADO_HISTORICO == "HISTORICO"
    assert ESTADO_ELIMINADO == "ELIMINADO"


def test_job_summary_includes_lifecycle_counters() -> None:
    from app.core.catalyst.models import JobSummary

    summary = JobSummary(registros_eliminados=5, entidades_inactivadas=2)
    payload = summary.to_callback_dict()
    assert payload["registrosEliminados"] == 5
    assert payload["entidadesInactivadas"] == 2


def test_job_summary_includes_eliminados_counter() -> None:
    from app.core.catalyst.models import JobSummary

    summary = JobSummary(registros_eliminados=3)
    assert summary.to_callback_dict()["registrosEliminados"] == 3


def test_qualified_table_uses_schema_name_prefix() -> None:
    assert qualified_table("s99998_dev", "a_3_identidad") == '"s99998_dev"."a_3_identidad"'


def test_derive_materialize_table_name_from_a1_prefix() -> None:
    assert derive_materialize_table_name("a_1_pma") == "a_4_pma"
    assert derive_materialize_table_name("custom_table") == "a_4_custom_table"


def test_tipo_dato_to_pg_type_maps_numeric_and_text() -> None:
    assert tipo_dato_to_pg_type("superficie") == "NUMERIC"
    assert tipo_dato_to_pg_type("moneda") == "NUMERIC"
    assert tipo_dato_to_pg_type("texto") == "TEXT"
    assert tipo_dato_to_pg_type("internal_id") == "TEXT"


def test_build_pivot_select_sql_includes_identity_and_typed_columns() -> None:
    config_rows = [
        ConfigRow(columna_origen="nombre", tipo_dato_generico="texto", es_llave=False, guardar=True),
        ConfigRow(
            columna_origen="superficie",
            tipo_dato_generico="superficie",
            es_llave=False,
            guardar=True,
        ),
        ConfigRow(
            columna_origen="ignorada",
            tipo_dato_generico="texto",
            es_llave=False,
            guardar=False,
        ),
    ]
    sql = build_pivot_select_sql(
        "s99998_dev",
        "a_3_boveda_kv",
        source_table="a_1_pma",
        config_rows=config_rows,
    )

    assert '"s99998_dev"."a_3_boveda_kv"' in sql
    assert "tabla_origen = 'a_1_pma'" in sql
    assert "estado = 'VIGENTE'" in sql
    assert "entidad_interna_id" in sql
    assert "llave_humana_completa" in sql
    assert "origen_dato" in sql
    assert "creado_por" in sql
    assert "actualizado_en" in sql
    assert '"nombre"' in sql
    assert '"superficie"' in sql
    assert "NULLIF" in sql
    assert "::NUMERIC" in sql
    assert "ignorada" not in sql


def test_build_pivot_select_sql_dedupes_duplicate_properties() -> None:
    config_rows = [
        ConfigRow(columna_origen="nombre", tipo_dato_generico="texto", es_llave=False, guardar=True),
        ConfigRow(columna_origen="nombre", tipo_dato_generico="texto", es_llave=True, guardar=True),
        ConfigRow(columna_origen="codigo", tipo_dato_generico="texto", es_llave=False, guardar=True),
    ]
    deduped = _dedupe_property_columns(config_rows)
    sql = build_pivot_select_sql(
        "s99998_dev",
        "a_3_boveda_kv",
        source_table="a_1_pma",
        config_rows=config_rows,
    )

    assert len(deduped) == 2
    assert sql.count('AS "nombre"') == 1
    assert 'AS "codigo"' in sql


def test_dedupe_property_columns_logs_warning_for_duplicates(caplog: pytest.LogCaptureFixture) -> None:
    config_rows = [
        ConfigRow(columna_origen="nombre", tipo_dato_generico="texto", es_llave=False, guardar=True),
        ConfigRow(columna_origen="nombre", tipo_dato_generico="texto", es_llave=False, guardar=True),
    ]

    deduped = _dedupe_property_columns(config_rows)

    assert len(deduped) == 1
    assert any("duplicada" in record.message for record in caplog.records)


def test_load_config_rows_filters_by_tabla_column(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.catalyst import config_reader

    executed: dict[str, object] = {}

    class FakeInspector:
        def has_table(self, table: str, schema: str) -> bool:
            return True

        def get_columns(self, table: str, schema: str) -> list[dict[str, str]]:
            return [
                {"name": "tabla"},
                {"name": "columna_origen"},
                {"name": "tipo_dato_generico"},
            ]

    class FakeConnection:
        def execute(self, sql: object, params: dict[str, object] | None = None) -> object:
            executed["sql"] = str(sql)
            executed["params"] = params

            class Result:
                def mappings(self) -> object:
                    return self

                def all(self) -> list[dict[str, object]]:
                    return [
                        {
                            "columna_origen": "nombre",
                            "tipo_dato_generico": "texto",
                            "es_llave": False,
                            "guardar": True,
                        }
                    ]

            return Result()

        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

    monkeypatch.setattr(config_reader, "get_db_engine", lambda: FakeEngine())
    monkeypatch.setattr(config_reader, "inspect", lambda _engine: FakeInspector())

    rows = config_reader.load_config_rows("s99998_dev", "a_1_pma", "a_2_config_ingesta_a")

    assert len(rows) == 1
    assert '"tabla" = :source_table' in str(executed["sql"])
    assert executed["params"] == {"source_table": "a_1_pma"}


def test_load_config_rows_requires_tabla_filter_column(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.catalyst import config_reader

    class FakeInspector:
        def has_table(self, table: str, schema: str) -> bool:
            return True

        def get_columns(self, table: str, schema: str) -> list[dict[str, str]]:
            return [{"name": "columna_origen"}]

    class FakeEngine:
        def connect(self) -> object:
            raise AssertionError("No debe consultar sin columna tabla")

    monkeypatch.setattr(config_reader, "get_db_engine", lambda: FakeEngine())
    monkeypatch.setattr(config_reader, "inspect", lambda _engine: FakeInspector())

    with pytest.raises(RuntimeError, match="tabla/tabla_origen"):
        config_reader.load_config_rows("s99998_dev", "a_1_pma", "a_2_config_ingesta_a")


def test_build_materialize_ddl_is_idempotent_drop_create() -> None:
    drop_sql, create_sql = build_materialize_ddl(
        "s99998_dev",
        "a_4_pma",
        "SELECT 1 AS entidad_interna_id",
    )
    assert drop_sql == 'DROP TABLE IF EXISTS "s99998_dev"."a_4_pma"'
    assert create_sql.startswith('CREATE TABLE "s99998_dev"."a_4_pma" AS ')


def test_materialize_payload_requires_target_table() -> None:
    payload = CatalystMaterializePayload.model_validate(
        {
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
            "targetTable": "a_4_pma",
            "configTable": "a_2_config_ingesta_a",
            "bovedaTable": "a_3_boveda_kv",
        }
    )
    assert payload.target_table == "a_4_pma"


def test_materialize_summary_callback_dict() -> None:
    from app.core.catalyst.models import MaterializeSummary

    summary = MaterializeSummary(entidades_materializadas=12, columnas_creadas=8)
    payload = summary.to_callback_dict()
    assert payload["entidadesMaterializadas"] == 12
    assert payload["columnasCreadas"] == 8


def test_catalyst_callback_payload_includes_n8n_fields() -> None:
    payload = CatalystJobPayload.model_validate(
        {
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
            "callbackUrl": "https://n8n.example.com/webhook/catalyst",
            **TABLE_FIELDS,
        }
    )
    engine = CatalystEngine(payload)
    engine._job_id = "job-123"
    engine._summary.filas_procesadas = 10

    body = engine._build_callback_payload("success")

    assert body["status"] == "success"
    assert body["jobId"] == "job-123"
    assert body["schemaName"] == "s99998_dev"
    assert body["targetTable"] == "a_3_boveda_kv"
    assert body["identidadTable"] == "a_3_identidad"
    assert body["callbackUrl"] == "https://n8n.example.com/webhook/catalyst"
    assert body["summary"]["filasProcesadas"] == 10


def test_catalyst_payload_accepts_camel_case() -> None:
    payload = CatalystJobPayload.model_validate(
        {
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "tabla_a",
            "callbackUrl": "https://n8n.example.com/webhook",
            "separadorLlave": "|",
            **TABLE_FIELDS,
        }
    )
    assert payload.schema_name == "s99998_dev"
    assert payload.source_table == "tabla_a"
    assert payload.resolve_tables().config_table == "a_2_config_ingesta_a"


def test_table_contract_uses_rms_defaults_without_job_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATALYST_CONFIG_TABLE", raising=False)
    monkeypatch.delenv("CATALYST_BOVEDA_TABLE", raising=False)
    monkeypatch.delenv("CATALYST_IDENTIDAD_TABLE", raising=False)

    contract = CatalystTableContract.from_job_fields(
        config_table=None,
        boveda_table=None,
        identidad_table=None,
    )
    assert contract.config_table == "a_2_config_ingesta_a"
    assert contract.boveda_table == "a_3_boveda_kv"
    assert contract.identidad_table == "a_3_identidad"


def test_catalyst_payload_resolves_rms_defaults_when_tables_omitted() -> None:
    payload = CatalystJobPayload.model_validate(
        {
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
        }
    )
    tables = payload.resolve_tables()
    assert tables.config_table == "a_2_config_ingesta_a"


def test_table_contract_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALYST_CONFIG_TABLE", "cfg_env")
    monkeypatch.setenv("CATALYST_BOVEDA_TABLE", "boveda_env")
    monkeypatch.setenv("CATALYST_IDENTIDAD_TABLE", "identidad_env")

    contract = CatalystTableContract.from_job_fields(
        config_table=None,
        boveda_table=None,
        identidad_table=None,
    )
    assert contract.config_table == "cfg_env"
    assert contract.boveda_table == "boveda_env"
    assert contract.identidad_table == "identidad_env"


def test_to_valor_original_preserves_raw_string() -> None:
    assert to_valor_original(" 12,5 m² ") == " 12,5 m² "
    assert to_valor_original(None) is None
    assert to_valor_original("   ") is None


def test_to_valor_limpio_normalizes_numeric_and_text_types() -> None:
    assert to_valor_limpio("12,5 m²", "superficie") == "12.5"
    assert to_valor_limpio("1.234,56", "numero") == "1.234.56"
    assert to_valor_limpio("  texto   con   espacios  ", "texto") == "texto con espacios"
    assert to_valor_limpio("10%", "porcentaje") == "10"
    assert to_valor_limpio("$1.200,50", "moneda") == "1.200.50"
    assert to_valor_limpio(None, "texto") is None


def test_to_valor_limpio_internal_id_rejects_empty() -> None:
    assert to_valor_limpio(" ABC-123 ", "internal_id") == "ABC-123"
    with pytest.raises(ValueError, match="internal_id no puede ser nulo"):
        to_valor_limpio("   ", "internal_id")


def test_is_empty_source_value_detects_blank_values() -> None:
    assert is_empty_source_value(None) is True
    assert is_empty_source_value("") is True
    assert is_empty_source_value("  ") is True
    assert is_empty_source_value("dato") is False


def test_normalize_numeric_string_extracts_digits_and_signs() -> None:
    assert normalize_numeric_string("abc-12,34xyz") == "-12.34"


def test_normalize_text_string_collapses_whitespace() -> None:
    assert normalize_text_string("  hola   mundo  ") == "hola mundo"


def test_compute_firma_auditoria_is_stable_and_handles_null() -> None:
    assert compute_firma_auditoria("12.5") == compute_firma_auditoria("12.5")
    assert compute_firma_auditoria("12.5") != compute_firma_auditoria("12.6")
    assert compute_firma_auditoria(None) == compute_firma_auditoria("")


def test_build_llave_humana_completa_concatenates_key_columns() -> None:
    row = {"codigo": "P-001", "zona": "Norte"}
    keys = [
        ConfigRow("zona", "texto", True, True),
        ConfigRow("codigo", "texto", True, True),
    ]
    assert build_llave_humana_completa(row, keys, "|") == "P-001|Norte"


def test_build_entidad_interna_id_is_deterministic() -> None:
    first = build_entidad_interna_id("Norte|P-001")
    second = build_entidad_interna_id("Norte|P-001")
    assert first == second
    assert first != build_entidad_interna_id("Sur|P-001")


def test_resolve_row_identity_from_es_llave_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.catalyst.identity.lookup_entidad_interna_id",
        lambda *args, **kwargs: None,
    )
    keys = [ConfigRow("codigo", "texto", True, True)]
    identity = resolve_row_identity(
        {"id": 99, "codigo": "ABC"},
        keys,
        "|",
        schema_name="s1",
        identidad_table="a_3_identidad",
        tabla_origen="a_1_pma",
    )
    assert identity.llave_humana_completa == "ABC"
    assert identity.entidad_interna_id == build_entidad_interna_id("ABC")


def test_resolve_row_identity_reuses_existing_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = "00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr(
        "app.core.catalyst.identity.lookup_entidad_interna_id",
        lambda *args, **kwargs: existing,
    )
    keys = [ConfigRow("codigo", "texto", True, True)]
    identity = resolve_row_identity(
        {"codigo": "ABC"},
        keys,
        "|",
        schema_name="s1",
        identidad_table="a_3_identidad",
        tabla_origen="a_1_pma",
    )
    assert identity.entidad_interna_id == existing


def test_resolve_row_identity_falls_back_to_row_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.catalyst.identity.lookup_entidad_interna_id",
        lambda *args, **kwargs: None,
    )
    identity = resolve_row_identity(
        {"id": 42, "nombre": "foo"},
        [],
        "|",
        schema_name="s1",
        identidad_table="a_3_identidad",
        tabla_origen="a_1_pma",
    )
    assert identity.llave_humana_completa == "ANCLA:42"
    assert identity.entidad_interna_id == build_entidad_interna_id("ANCLA:42")


def test_is_req_aceptado_accepts_common_truthy_values() -> None:
    assert is_req_aceptado(True) is True
    assert is_req_aceptado("true") is True
    assert is_req_aceptado("1") is True
    assert is_req_aceptado(False) is False
    assert is_req_aceptado(None) is False
    assert is_req_aceptado("false") is False


def test_row_passes_acceptance_filter_when_column_missing() -> None:
    row = {"id": 1, "nombre": "foo"}
    assert row_passes_acceptance_filter(row, has_req_aceptado=False) is True


def test_row_passes_acceptance_filter_requires_truthy_req_aceptado() -> None:
    assert row_passes_acceptance_filter({"req_aceptado": True}, has_req_aceptado=True) is True
    assert row_passes_acceptance_filter({"req_aceptado": False}, has_req_aceptado=True) is False


def test_source_table_has_column_is_case_insensitive() -> None:
    assert source_table_has_column({"ID", "req_aceptado"}, "REQ_ACEPTADO") is True
    assert source_table_has_column({"id"}, "nombre") is False


def test_catalyst_job_endpoint_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    class FailingEngine:
        def __init__(self, payload: CatalystJobPayload) -> None:
            raise RuntimeError("Tabla de configuración no encontrada")

        def run(self, job_id: str | None = None) -> None:
            return None

    monkeypatch.setattr("app.api.catalyst_endpoints.CatalystEngine", FailingEngine)

    client = TestClient(app)
    response = client.post(
        "/api/v1/catalyst/job",
        json={
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
        },
    )

    body = response.json()
    assert response.status_code == 400
    assert body["status"] == "error"
    assert body["errorType"] == "catalyst_job_rejected"
    assert "Tabla de configuración no encontrada" in body["error"]
    assert "jobId" in body


def test_catalyst_job_endpoint_accepts_job_with_rms_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    class NoopEngine:
        def __init__(self, payload: CatalystJobPayload) -> None:
            self.payload = payload

        def run(self, job_id: str | None = None) -> None:
            return None

    monkeypatch.setattr("app.api.catalyst_endpoints.CatalystEngine", NoopEngine)

    client = TestClient(app)
    response = client.post(
        "/api/v1/catalyst/job",
        json={
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
        },
    )

    body = response.json()
    assert response.status_code == 202
    assert body["status"] == "accepted"
    assert body["configTable"] == "a_2_config_ingesta_a"
    assert body["bovedaTable"] == "a_3_boveda_kv"
    assert body["identidadTable"] == "a_3_identidad"


def test_catalyst_materialize_endpoint_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from main import app

    class FailingEngine:
        def __init__(self, payload: CatalystMaterializePayload) -> None:
            raise RuntimeError("Tabla de configuración no encontrada")

        def run(self, job_id: str | None = None) -> None:
            return None

    monkeypatch.setattr(
        "app.api.catalyst_endpoints.CatalystMaterializeEngine",
        FailingEngine,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/catalyst/materialize",
        json={
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
            "targetTable": "a_4_pma",
        },
    )

    body = response.json()
    assert response.status_code == 400
    assert body["status"] == "error"
    assert body["errorType"] == "catalyst_materialize_rejected"
    assert "Tabla de configuración no encontrada" in body["error"]
    assert "jobId" in body


def test_catalyst_materialize_endpoint_accepts_job_with_rms_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from main import app

    class NoopEngine:
        def __init__(self, payload: CatalystMaterializePayload) -> None:
            self.payload = payload

        def run(self, job_id: str | None = None) -> None:
            return None

    monkeypatch.setattr(
        "app.api.catalyst_endpoints.CatalystMaterializeEngine",
        NoopEngine,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/catalyst/materialize",
        json={
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "a_1_pma",
            "targetTable": "a_4_pma",
        },
    )

    body = response.json()
    assert response.status_code == 202
    assert body["status"] == "accepted"
    assert body["targetTable"] == "a_4_pma"
    assert body["configTable"] == "a_2_config_ingesta_a"
    assert body["bovedaTable"] == "a_3_boveda_kv"


def test_sync_back_payload_validates_email() -> None:
    with pytest.raises(ValueError, match="email"):
        CatalystSyncBackPayload.model_validate(
            {
                "sourceTable": "a_1_pma",
                "entidadInternaId": "uuid-123",
                "propiedad": "nombre",
                "nuevoValor": "Nuevo",
                "usuarioEmail": "invalido",
            }
        )


def test_sync_back_payload_accepts_v16_fields() -> None:
    payload = CatalystSyncBackPayload.model_validate(
        {
            "sourceTable": "a_1_pma",
            "entidadInternaId": "550e8400-e29b-41d4-a716-446655440000",
            "propiedad": "superficie",
            "nuevoValor": "125.5",
            "usuarioEmail": "arquitecto@example.com",
            "configTable": "a_2_config_ingesta_a",
            "bovedaTable": "a_3_boveda_kv",
            "identidadTable": "a_3_identidad",
        }
    )
    assert payload.propiedad == "superficie"
    assert payload.usuario_email == "arquitecto@example.com"


def test_sync_back_payload_accepts_legacy_field_aliases() -> None:
    payload = CatalystSyncBackPayload.model_validate(
        {
            "sourceTable": "a_1_pma",
            "entidadInternaId": "550e8400-e29b-41d4-a716-446655440000",
            "propiedadOrigen": "nombre",
            "nuevoValor": "Legacy",
            "creadoPor": "legacy@example.com",
        }
    )
    assert payload.propiedad == "nombre"
    assert payload.usuario_email == "legacy@example.com"


def test_catalyst_sync_back_endpoint_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from main import app

    class FailingEngine:
        def __init__(self, payload: CatalystSyncBackPayload) -> None:
            raise ValueError("Entidad no encontrada")

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        "app.api.catalyst_endpoints.CatalystSyncBackEngine",
        FailingEngine,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/catalyst/sync-back",
        json={
            "sourceTable": "a_1_pma",
            "entidadInternaId": "550e8400-e29b-41d4-a716-446655440000",
            "propiedad": "nombre",
            "nuevoValor": "Actualizado",
            "usuarioEmail": "user@example.com",
        },
    )

    body = response.json()
    assert response.status_code == 400
    assert body["status"] == "error"
    assert body["errorType"] == "catalyst_sync_back_rejected"
    assert "Entidad no encontrada" in body["error"]


def test_catalyst_sync_back_endpoint_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.catalyst.models import SyncBackSummary
    from main import app

    class SuccessEngine:
        def __init__(self, payload: CatalystSyncBackPayload) -> None:
            self.payload = payload

        def run(self) -> SyncBackSummary:
            return SyncBackSummary(registros_actualizados=1)

    monkeypatch.setattr(
        "app.api.catalyst_endpoints.CatalystSyncBackEngine",
        SuccessEngine,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/catalyst/sync-back",
        json={
            "sourceTable": "a_1_pma",
            "entidadInternaId": "550e8400-e29b-41d4-a716-446655440000",
            "propiedad": "nombre",
            "nuevoValor": "Actualizado",
            "usuarioEmail": "user@example.com",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["origenDato"] == "edicion_manual"
    assert body["usuarioEmail"] == "user@example.com"
    assert body["summary"]["registrosActualizados"] == 1
