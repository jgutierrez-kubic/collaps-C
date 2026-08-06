"""Tests del módulo Catalyst (COLLAPS v1.3)."""

from __future__ import annotations

from app.core.catalyst.boveda_writer import compute_firma_valor
from app.core.catalyst.canonical import build_cinco_casillas
from app.core.catalyst.cleanup import clean_value
from app.core.catalyst.governance import (
    is_req_aceptado,
    row_passes_acceptance_filter,
    source_table_has_column,
)
from app.core.catalyst.identity import aggressive_normalize, build_clave_cotejo, resolve_row_identity
from app.core.catalyst.models import ConfigRow
from app.models.catalyst_payload import CatalystJobPayload


def test_catalyst_payload_accepts_camel_case() -> None:
    payload = CatalystJobPayload.model_validate(
        {
            "source": "n8n",
            "schemaName": "s99998_dev",
            "sourceTable": "tabla_a",
            "callbackUrl": "https://n8n.example.com/webhook",
            "separadorLlave": "|",
        }
    )
    assert payload.schema_name == "s99998_dev"
    assert payload.source_table == "tabla_a"
    assert payload.separador_llave == "|"


def test_aggressive_normalize_strips_accents_and_spaces() -> None:
    assert aggressive_normalize("  Café de Ñoño  ") == "CAFEDENONO"


def test_build_clave_cotejo_concatenates_ordered_keys() -> None:
    row = {"codigo": "P-001", "zona": "Norte"}
    keys = [
        ConfigRow(
            propiedad="zona",
            rol="llave_humana",
            orden_llave=2,
            formato_entrada="texto",
            regla_limpieza="",
            unidad_esperada="",
            parametro="",
            guardar=True,
        ),
        ConfigRow(
            propiedad="codigo",
            rol="llave_humana",
            orden_llave=1,
            formato_entrada="texto",
            regla_limpieza="",
            unidad_esperada="",
            parametro="",
            guardar=True,
        ),
    ]
    assert build_clave_cotejo(row, keys, "|") == "P-001|NORTE"


def test_compute_firma_valor_is_stable() -> None:
    assert compute_firma_valor("hola") == compute_firma_valor("hola")
    assert compute_firma_valor("hola") != compute_firma_valor("hola!")


def test_build_cinco_casillas_for_requisito() -> None:
    config = ConfigRow(
        propiedad="espesor",
        rol="requisito",
        orden_llave=0,
        formato_entrada="numero",
        regla_limpieza="",
        unidad_esperada="mm",
        parametro="min=10",
        guardar=True,
    )
    casillas = build_cinco_casillas(config, 12.5)
    assert casillas["casilla_1_clave"] == "espesor"
    assert casillas["casilla_2_valor"] == 12.5
    assert casillas["casilla_3_unidad"] == "mm"
    assert casillas["casilla_4_formato"] == "numero"
    assert casillas["casilla_5_parametro"] == "min=10"


def test_clean_value_coerces_numero() -> None:
    config = ConfigRow(
        propiedad="cantidad",
        rol="valor",
        orden_llave=0,
        formato_entrada="numero",
        regla_limpieza="",
        unidad_esperada="",
        parametro="",
        guardar=True,
    )
    assert clean_value("12,5", config) == 12.5


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


def test_resolve_row_identity_uses_ancla_when_no_llave_humana() -> None:
    identity = resolve_row_identity({"id": 42, "nombre": "foo"}, [], "|")
    assert identity.ancla_origen == "42"
    assert identity.clave_cotejo == "ANCLA:42"


def test_resolve_row_identity_prefers_llave_humana_when_present() -> None:
    keys = [
        ConfigRow(
            propiedad="codigo",
            rol="llave_humana",
            orden_llave=1,
            formato_entrada="texto",
            regla_limpieza="",
            unidad_esperada="",
            parametro="",
            guardar=True,
        )
    ]
    identity = resolve_row_identity({"id": 99, "codigo": "ABC"}, keys, "|")
    assert identity.clave_cotejo == "ABC"
    assert identity.ancla_origen == "99"
