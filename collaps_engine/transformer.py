"""Orquestador principal de transformaciones COLLAPS."""

from __future__ import annotations

from typing import Any, Optional

from collaps_engine.comparison_engine import OPERATIONS_REGISTRY

TransformationResult = dict[str, Any]


def _infer_is_match(method_id: str, result_value: Any, options: dict) -> Optional[bool]:
    if isinstance(result_value, bool):
        return result_value

    if isinstance(result_value, dict):
        if "is_within_tolerance" in result_value:
            return bool(result_value["is_within_tolerance"])
        if method_id == "null_check":
            return not result_value.get("any_null", True)

    if method_id in {"fuzzy_levenshtein", "fuzzy_jaro_winkler", "array_jaccard"}:
        threshold = options.get("threshold", 0.85)
        if isinstance(result_value, (int, float)):
            return float(result_value) >= float(threshold)

    return None


def execute_transformation(
    val_a: Any,
    val_b: Any,
    method_id: str,
    options: Optional[dict] = None,
) -> TransformationResult:
    """Ejecuta una operación del registro y devuelve un resultado estandarizado."""
    options = options or {}

    if method_id not in OPERATIONS_REGISTRY:
        return {
            "method_id": method_id,
            "result_value": None,
            "is_match": None,
            "metadata": {"options": options},
            "error": f"Método no registrado: '{method_id}'",
        }

    operation = OPERATIONS_REGISTRY[method_id]

    try:
        result_value = operation(val_a, val_b, options)
        is_match = _infer_is_match(method_id, result_value, options)

        return {
            "method_id": method_id,
            "result_value": result_value,
            "is_match": is_match,
            "metadata": {"options": options},
            "error": None,
        }
    except Exception as exc:
        return {
            "method_id": method_id,
            "result_value": None,
            "is_match": None,
            "metadata": {"options": options},
            "error": str(exc),
        }
