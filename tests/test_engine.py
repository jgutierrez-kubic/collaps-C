"""Suite de tests para collaps_engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from collaps_engine.comparison_engine import OPERATIONS_REGISTRY
from collaps_engine.datetime_parser import (
    date_diff_days,
    date_diff_seconds,
    date_equal,
    date_tolerance,
    parse_to_utc_datetime,
)
from collaps_engine.transformer import execute_transformation


# --- datetime_parser ---


def test_parse_iso_with_z_suffix() -> None:
    result = parse_to_utc_datetime("2024-01-15T10:30:00Z")
    assert result == datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)


def test_parse_standard_datetime_string() -> None:
    result = parse_to_utc_datetime("2024-01-15 10:30:00")
    assert result.year == 2024 and result.month == 1 and result.day == 15


def test_parse_unix_milliseconds() -> None:
    result = parse_to_utc_datetime(1_700_000_000_000)
    assert result.tzinfo == timezone.utc


def test_date_diff_seconds_and_days() -> None:
    a = "2024-01-01T00:00:00Z"
    b = "2024-01-02T00:00:00Z"
    assert date_diff_seconds(a, b) == 86_400.0
    assert date_diff_days(a, b) == 1.0


def test_date_equal_ignores_time() -> None:
    assert date_equal("2024-01-01 00:00:00", "2024-01-01 23:59:59") is True


def test_date_tolerance() -> None:
    result = date_tolerance("2024-01-01T00:00:00Z", "2024-01-01T00:00:30Z", 60)
    assert result["is_within_tolerance"] is True
    assert result["delta_seconds"] == 30.0


# --- A. Numéricas ---


@pytest.mark.parametrize(
    ("method_id", "a", "b", "expected"),
    [
        ("math_add", 10, 5, 15.0),
        ("math_sub", 10, 5, 5.0),
        ("math_diff_abs", 10, 7, 3.0),
        ("math_diff_pct", 100, 80, 20.0),
        ("math_ratio", 10, 2, 5.0),
    ],
)
def test_numeric_operations(method_id: str, a: float, b: float, expected: float) -> None:
    result = execute_transformation(a, b, method_id)
    assert result["error"] is None
    assert result["result_value"] == expected


def test_math_tolerance_within_epsilon() -> None:
    result = execute_transformation(100, 102, "math_tolerance", {"epsilon": 5})
    assert result["result_value"]["is_within_tolerance"] is True
    assert result["is_match"] is True


def test_math_diff_pct_division_by_zero() -> None:
    result = execute_transformation(0, 5, "math_diff_pct")
    assert result["result_value"] == float("inf")


# --- B. Texto ---


def test_strict_equal() -> None:
    result = execute_transformation("Hola", "Hola", "strict_equal")
    assert result["result_value"] is True
    assert result["is_match"] is True


def test_normalized_equal_removes_accents_and_case() -> None:
    result = execute_transformation("Café", "cafe", "normalized_equal")
    assert result["result_value"] is True


def test_fuzzy_levenshtein_similarity() -> None:
    result = execute_transformation("kitten", "sitting", "fuzzy_levenshtein")
    assert 0.0 <= result["result_value"] <= 1.0


def test_fuzzy_jaro_winkler_identical() -> None:
    result = execute_transformation("martha", "martha", "fuzzy_jaro_winkler")
    assert result["result_value"] == 1.0


def test_contains_check() -> None:
    result = execute_transformation("world", "hello world", "contains_check")
    assert result["result_value"] is True


def test_regex_match() -> None:
    result = execute_transformation("abc-123", "", "regex_match", {"pattern": r"^[a-z]+-\d+$"})
    assert result["result_value"] is True


# --- C. Fechas vía registry ---


def test_date_operations_via_transformer() -> None:
    a = "2024-01-01T00:00:00Z"
    b = "2024-01-03T00:00:00Z"
    assert execute_transformation(a, b, "date_diff_days")["result_value"] == 2.0
    assert execute_transformation(a, b, "date_equal")["result_value"] is False
    tolerance = execute_transformation(a, b, "date_tolerance", {"tolerance_seconds": 200_000})
    assert tolerance["result_value"]["is_within_tolerance"] is True


# --- D. Arrays ---


def test_array_intersection() -> None:
    result = execute_transformation([1, 2, 3], [2, 3, 4], "array_intersection")
    assert result["result_value"] == [2, 3]


def test_array_difference() -> None:
    result = execute_transformation([1, 2, 3], [2], "array_difference")
    assert result["result_value"] == [1, 3]


def test_array_jaccard() -> None:
    result = execute_transformation([1, 2], [2, 3], "array_jaccard")
    assert result["result_value"] == pytest.approx(1 / 3)


# --- E. Lógica ---


def test_null_check() -> None:
    result = execute_transformation(None, 1, "null_check")
    assert result["result_value"]["any_null"] is True
    assert result["is_match"] is False


def test_boolean_logic_and_or_xor() -> None:
    assert execute_transformation(True, False, "boolean_logic", {"operator": "AND"})["result_value"] is False
    assert execute_transformation(True, False, "boolean_logic", {"operator": "OR"})["result_value"] is True
    assert execute_transformation(True, False, "boolean_logic", {"operator": "XOR"})["result_value"] is True


# --- Orquestador ---


def test_execute_transformation_unknown_method() -> None:
    result = execute_transformation(1, 2, "unknown_method")
    assert result["error"] is not None
    assert "no registrado" in result["error"]


def test_operations_registry_contains_all_families() -> None:
    expected = {
        "math_add",
        "strict_equal",
        "date_diff_seconds",
        "array_jaccard",
        "null_check",
    }
    assert expected.issubset(OPERATIONS_REGISTRY.keys())
