"""Tests del módulo Catalyst (RMS Genérico v1.4)."""

from __future__ import annotations

import os

import pytest

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
from app.core.catalyst.models import ConfigRow
from app.core.catalyst.table_contract import CatalystTableContract
from app.models.catalyst_payload import CatalystJobPayload

TABLE_FIELDS = {
    "configTable": "a_2_config_ingesta_a",
    "bovedaTable": "a_3_boveda_kv",
    "identidadTable": "a_2_identidad",
}


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


def test_table_contract_resolves_from_env_when_job_omits_tables(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_table_contract_requires_job_or_env() -> None:
    env_backup = {
        "CATALYST_CONFIG_TABLE": os.environ.pop("CATALYST_CONFIG_TABLE", None),
        "CATALYST_BOVEDA_TABLE": os.environ.pop("CATALYST_BOVEDA_TABLE", None),
        "CATALYST_IDENTIDAD_TABLE": os.environ.pop("CATALYST_IDENTIDAD_TABLE", None),
    }
    try:
        with pytest.raises(ValueError, match="Nombre de tabla requerido"):
            CatalystTableContract.from_job_fields(
                config_table=None,
                boveda_table=None,
                identidad_table=None,
            )
    finally:
        for key, value in env_backup.items():
            if value is not None:
                os.environ[key] = value


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
        identidad_table="a_2_identidad",
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
        identidad_table="a_2_identidad",
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
        identidad_table="a_2_identidad",
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
