"""Tests para el generador SQL de análisis."""

from __future__ import annotations

from app.core.query_builder import build_analysis_sql, sql_source_column_alias
from app.models.payload import AnalysisPayload


def _payload(**overrides: object) -> AnalysisPayload:
    base: dict[str, object] = {
        "tableA": "tabla_a",
        "tableB": "tabla_b",
        "joinKeyA": "llave_a",
        "joinKeyB": "llave_b",
        "columnsA": "val",
        "columnsB": "val",
        "calculationMethods": "math_add",
        "targetTable": "c_results_test",
        "schemaName": "s99998_dev",
    }
    base.update(overrides)
    return AnalysisPayload.model_validate(base)


def test_sql_source_column_alias_is_indexed() -> None:
    assert sql_source_column_alias(0, "val", "a") == "0_val_a"
    assert sql_source_column_alias(1, "val", "b") == "1_val_b"


def test_build_analysis_sql_unique_aliases_for_repeated_column() -> None:
    payload = _payload(
        columnsA="val, val",
        columnsB="val, val",
        calculationMethods="math_add, math_sub",
    )

    sql = build_analysis_sql(payload)

    assert '"0_val_a"' in sql
    assert '"0_val_b"' in sql
    assert '"1_val_a"' in sql
    assert '"1_val_b"' in sql
    assert sql.count('"0_val_a"') == 1
    assert sql.count('"1_val_a"') == 1
