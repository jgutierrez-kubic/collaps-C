"""Integration tests for analysis_engine + collaps_engine."""

from __future__ import annotations

import pandas as pd

from app.core.analysis_engine import AnalysisEngine
from app.models.payload import AnalysisPayload


def _condenser_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tableA": "contrato",
        "tableB": "modelo",
        "joinKeyA": "id",
        "joinKeyB": "id",
        "columnsA": "cantidad",
        "columnsB": "cantidad",
        "calculationMethods": "DIFERENCIA",
        "targetTable": "c_results_precioFrutas",
    }
    base.update(overrides)
    return base


def test_analysis_payload_accepts_camel_case_json() -> None:
    payload = AnalysisPayload.model_validate(_condenser_payload())
    assert payload.table_a == "contrato"
    assert payload.join_key_a == "id"
    assert payload.calculation_methods == "DIFERENCIA"
    assert payload.target_table == "c_results_precioFrutas"


def test_apply_collaps_transformations_legacy_diferencia() -> None:
    payload = AnalysisPayload.model_validate(_condenser_payload())
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"cantidad_a": [10.0, 20.0], "cantidad_b": [10.0, 25.0]})

    result = engine._apply_collaps_transformations(df)

    assert "0_cantidadA" in result.columns
    assert "0_cantidadB" in result.columns
    assert "0_metodo_aplicado" in result.columns
    assert "0_diferencia" in result.columns
    assert result["0_diferencia"].tolist() == [0.0, 5.0]
    assert "cantidad_a" not in result.columns


def test_apply_collaps_transformations_vectorized_math_add() -> None:
    payload = AnalysisPayload.model_validate(
        _condenser_payload(calculationMethods="math_add", targetTable="resultados")
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"cantidad_a": [10.0, 20.0, None], "cantidad_b": [5.0, 8.0, 3.0]})

    result = engine._apply_collaps_transformations(df)

    assert "0_math_add" in result.columns
    assert result["0_math_add"].iloc[0] == 15.0
    assert result["0_math_add"].iloc[1] == 28.0
    assert pd.isna(result["0_math_add"].iloc[2])


def test_apply_collaps_transformations_registry_method() -> None:
    payload = AnalysisPayload.model_validate(
        _condenser_payload(
            columnsA="nombre",
            columnsB="nombre",
            calculationMethods="fuzzy_levenshtein",
            targetTable="resultados",
        )
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"nombre_a": ["hola"], "nombre_b": ["hola"]})

    result = engine._apply_collaps_transformations(df)

    assert "0_nombreA" in result.columns
    assert "0_nombreB" in result.columns
    assert "0_fuzzy_levenshtein" in result.columns
    assert result["0_fuzzy_levenshtein"].iloc[0] == 1.0
    assert "0_is_match" in result.columns


def test_apply_collaps_transformations_multiple_pairs_indexed() -> None:
    payload = AnalysisPayload.model_validate(
        _condenser_payload(
            columnsA="nombre, cantidad",
            columnsB="nombre, cantidad",
            calculationMethods="IGUALDAD, DIFERENCIA",
            targetTable="resultados",
        )
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame(
        {
            "nombre_a": ["a"],
            "nombre_b": ["a"],
            "cantidad_a": [10.0],
            "cantidad_b": [12.0],
        }
    )

    result = engine._apply_collaps_transformations(df)

    assert "0_nombreA" in result.columns
    assert "1_cantidadA" in result.columns
    assert "1_diferencia" in result.columns


def test_apply_collaps_transformations_skips_is_match_for_pure_boolean() -> None:
    payload = AnalysisPayload.model_validate(
        _condenser_payload(calculationMethods="IGUALDAD", targetTable="resultados")
    )
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"cantidad_a": [10.0, 20.0], "cantidad_b": [10.0, 25.0]})

    result = engine._apply_collaps_transformations(df)

    assert "0_igualdad" in result.columns
    assert result["0_igualdad"].tolist() == [True, False]
    assert "0_is_match" not in result.columns


def test_merge_chunk_into_summary_accumulates_counts() -> None:
    summary = AnalysisEngine._init_analytical_summary()
    chunk_one = pd.DataFrame({"estado_cruce": ["Match", "Only A"]})
    chunk_two = pd.DataFrame({"estado_cruce": ["Only B", "Match"]})

    summary = AnalysisEngine._merge_chunk_into_summary(summary, chunk_one)
    summary = AnalysisEngine._merge_chunk_into_summary(summary, chunk_two)
    summary = AnalysisEngine._finalize_analytical_summary(
        summary,
        {"total_a": 2, "unique_a": 2, "total_b": 2, "unique_b": 2},
    )

    assert summary["totalRows"] == 4
    assert summary["matches"] == 2
    assert summary["onlyA"] == 1
    assert summary["onlyB"] == 1
    assert summary["hasDuplicates"] is False


def test_reorder_columns_for_persist_moves_metadata_to_end() -> None:
    df = pd.DataFrame(
        {
            "run_id": [1],
            "0_cantidadA": [10.0],
            "estado_cruce": ["Match"],
            "llave_cruce": ["abc"],
            "created_at": [pd.Timestamp("2024-01-01", tz="UTC")],
            "job_id": ["job-1"],
        }
    )

    reordered = AnalysisEngine._reorder_columns_for_persist(df)

    assert reordered.columns.tolist() == [
        "0_cantidadA",
        "llave_cruce",
        "run_id",
        "created_at",
        "job_id",
        "estado_cruce",
    ]


def test_allocate_next_run_id_returns_one_for_missing_table(monkeypatch) -> None:
    monkeypatch.setattr(
        AnalysisEngine,
        "_table_exists",
        staticmethod(lambda *_args, **_kwargs: False),
    )
    assert AnalysisEngine._allocate_next_run_id(object(), "schema", "tabla") == 1


def test_build_analytical_summary_with_duplicates_flag() -> None:
    df = pd.DataFrame(
        {
            "estado_cruce": ["Match", "Match", "Only A", "Only B"],
        }
    )
    source_stats = {"total_a": 4, "unique_a": 2, "total_b": 3, "unique_b": 3}

    summary = AnalysisEngine._build_analytical_summary(df, source_stats)

    assert summary["totalRows"] == 4
    assert summary["matches"] == 2
    assert summary["onlyA"] == 1
    assert summary["onlyB"] == 1
    assert summary["hasDuplicates"] is True


def test_auto_migrate_table_returns_true_when_columns_added(monkeypatch) -> None:
    engine = AnalysisEngine(AnalysisPayload.model_validate(_condenser_payload()))
    df = pd.DataFrame({"new_col": [1]})

    monkeypatch.setattr(AnalysisEngine, "_table_exists", staticmethod(lambda *_a, **_k: True))
    monkeypatch.setattr(
        AnalysisEngine,
        "_get_existing_columns",
        staticmethod(lambda *_a, **_k: {"run_id"}),
    )

    class FakeConn:
        def execute(self, _ddl):
            return None

    assert engine._auto_migrate_table(FakeConn(), "schema", "tabla", df) is True
    assert engine.update_schema is False


def test_persist_chunk_sets_update_schema_on_replace(monkeypatch) -> None:
    payload = AnalysisPayload.model_validate(_condenser_payload())
    engine = AnalysisEngine(payload)
    df = pd.DataFrame({"cantidad_a": [1.0], "cantidad_b": [2.0]})

    monkeypatch.setattr(AnalysisEngine, "_auto_migrate_table", lambda *_a, **_k: False)

    class FakeConn:
        pass

    class FakeBegin:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, *_args):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    monkeypatch.setattr("app.core.analysis_engine.get_db_engine", lambda: FakeEngine())
    monkeypatch.setattr(
        pd.DataFrame,
        "to_sql",
        lambda self, *args, **kwargs: None,
    )

    engine._persist_chunk(
        df,
        run_id=1,
        created_at=pd.Timestamp("2024-01-01", tz="UTC"),
        job_id="job-1",
        if_exists="replace",
        migrate=False,
    )

    assert engine.update_schema is True
    assert engine._filas_insertadas == 1


def test_result_column_name_same_columns() -> None:
    name = AnalysisEngine._result_column_name("Cantidad", "cantidad", "math_sub")
    assert name == "cantidad__math_sub"


def test_result_column_name_different_columns() -> None:
    name = AnalysisEngine._result_column_name("Cantidad", "Cantidad/Modelo", "math_sub")
    assert name == "cantidad__vs__cantidad_modelo__math_sub"
