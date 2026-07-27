"""Tests de integración analysis_engine + collaps_engine."""

from __future__ import annotations

import httpx
import pandas as pd

from app.core.analysis_engine import AnalysisEngine
from app.models.payload import AnalysisPayload


def test_apply_collaps_transformations_legacy_diferencia() -> None:
    payload = AnalysisPayload.model_validate(
        {
            "tabla_a": "contrato",
            "tabla_b": "modelo",
            "llave_cruce_a": "id",
            "llave_cruce_b": "id",
            "columnas_a": "cantidad",
            "columnas_b": "cantidad",
            "metodos_calculo": "DIFERENCIA",
            "tabla_destino": "resultados",
        }
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"cantidad_a": [10.0, 20.0], "cantidad_b": [10.0, 25.0]})

    result = engine._apply_collaps_transformations(df)

    assert "cantidad__math_sub" in result.columns
    assert result["cantidad__math_sub"].tolist() == [0.0, 5.0]


def test_apply_collaps_transformations_registry_method() -> None:
    payload = AnalysisPayload.model_validate(
        {
            "tabla_a": "contrato",
            "tabla_b": "modelo",
            "llave_cruce_a": "id",
            "llave_cruce_b": "id",
            "columnas_a": "nombre",
            "columnas_b": "nombre",
            "metodos_calculo": "fuzzy_levenshtein",
            "tabla_destino": "resultados",
        }
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"nombre_a": ["hola"], "nombre_b": ["hola"]})

    result = engine._apply_collaps_transformations(df)

    assert "nombre__fuzzy_levenshtein" in result.columns
    assert result["nombre__fuzzy_levenshtein"].iloc[0] == 1.0
    assert "is_match__nombre__fuzzy_levenshtein" in result.columns


def test_apply_collaps_transformations_skips_is_match_for_pure_boolean() -> None:
    payload = AnalysisPayload.model_validate(
        {
            "tabla_a": "contrato",
            "tabla_b": "modelo",
            "llave_cruce_a": "id",
            "llave_cruce_b": "id",
            "columnas_a": "cantidad",
            "columnas_b": "cantidad",
            "metodos_calculo": "IGUALDAD",
            "tabla_destino": "resultados",
        }
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"cantidad_a": [10.0, 20.0], "cantidad_b": [10.0, 25.0]})

    result = engine._apply_collaps_transformations(df)

    assert "cantidad__strict_equal" in result.columns
    assert result["cantidad__strict_equal"].tolist() == [True, False]
    assert not any(col.startswith("is_match__") for col in result.columns)


def test_build_analytical_summary_with_duplicates_flag() -> None:
    df = pd.DataFrame(
        {
            "estado_cruce": ["Match", "Match", "Only A", "Only B"],
        }
    )
    source_stats = {"total_a": 4, "unique_a": 2, "total_b": 3, "unique_b": 3}

    summary = AnalysisEngine._build_analytical_summary(df, source_stats)

    assert summary["total_rows"] == 4
    assert summary["matches"] == 2
    assert summary["only_a"] == 1
    assert summary["only_b"] == 1
    assert summary["has_duplicates"] is True


def test_result_column_name_same_columns() -> None:
    name = AnalysisEngine._result_column_name("Cantidad", "cantidad", "math_sub")
    assert name == "cantidad__math_sub"


def test_result_column_name_different_columns() -> None:
    name = AnalysisEngine._result_column_name("Cantidad", "Cantidad/Modelo", "math_sub")
    assert name == "cantidad__vs__cantidad_modelo__math_sub"


def test_fetch_directus_credentials_returns_tuple() -> None:
    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "directus_url": "https://directus.example.com/",
                "Instance_Token": "secret-token",
            }

    class FakeConn:
        def execute(self, _query, _params):
            return FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConn()

    creds = AnalysisEngine._fetch_directus_credentials(FakeEngine(), "s00001_incancer")
    assert creds == ("https://directus.example.com/", "secret-token")


def test_fetch_directus_credentials_missing_row_returns_none() -> None:
    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return None

    class FakeConn:
        def execute(self, _query, _params):
            return FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConn()

    assert AnalysisEngine._fetch_directus_credentials(FakeEngine(), "unknown_schema") is None


def test_fetch_directus_credentials_empty_values_returns_none() -> None:
    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {"directus_url": "", "Instance_Token": "token"}

    class FakeConn:
        def execute(self, _query, _params):
            return FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConn()

    assert AnalysisEngine._fetch_directus_credentials(FakeEngine(), "s00001_incancer") is None


def test_is_directus_collection_exists_error_detects_invalid_payload() -> None:
    response = httpx.Response(
        400,
        json={
            "errors": [
                {
                    "message": 'Collection "c_resultados" already exists',
                    "extensions": {"code": "INVALID_PAYLOAD"},
                }
            ]
        },
        request=httpx.Request("POST", "https://directus.example.com/collections"),
    )
    exc = httpx.HTTPStatusError("error", request=response.request, response=response)

    assert AnalysisEngine._is_directus_collection_exists_error(exc) is True


def test_is_directus_collection_exists_error_ignores_other_status_codes() -> None:
    response = httpx.Response(
        403,
        json={"errors": [{"message": "Forbidden", "extensions": {"code": "FORBIDDEN"}}]},
        request=httpx.Request("POST", "https://directus.example.com/collections"),
    )
    exc = httpx.HTTPStatusError("error", request=response.request, response=response)

    assert AnalysisEngine._is_directus_collection_exists_error(exc) is False

