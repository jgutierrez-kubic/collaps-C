"""Motor de comparaciones deterministas — registro de operaciones puras."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any, Callable

from collaps_engine.datetime_parser import (
    date_diff_days,
    date_diff_seconds,
    date_equal,
    date_tolerance,
    parse_to_utc_datetime,
)

OperationFn = Callable[..., Any]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_text(value: Any) -> str:
    text = _to_str(value).strip().lower()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "si", "sí"}
    return bool(value)


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (char_a != char_b)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _jaro_similarity(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    match_distance = max(0, match_distance)

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    transpositions /= 2
    return (
        matches / len1
        + matches / len2
        + (matches - transpositions) / matches
    ) / 3.0


def _jaro_winkler_similarity(s1: str, s2: str, prefix_scale: float = 0.1) -> float:
    jaro = _jaro_similarity(s1, s2)
    prefix = 0
    for char_a, char_b in zip(s1, s2):
        if char_a != char_b:
            break
        prefix += 1
    prefix = min(4, prefix)
    return jaro + prefix * prefix_scale * (1.0 - jaro)


# --- A. Numéricas y Tolerancia ---


def math_add(val_a: Any, val_b: Any, options: dict | None = None) -> float | None:
    a, b = _to_float(val_a), _to_float(val_b)
    if a is None or b is None:
        return None
    return a + b


def math_sub(val_a: Any, val_b: Any, options: dict | None = None) -> float | None:
    a, b = _to_float(val_a), _to_float(val_b)
    if a is None or b is None:
        return None
    return a - b


def math_diff_abs(val_a: Any, val_b: Any, options: dict | None = None) -> float | None:
    a, b = _to_float(val_a), _to_float(val_b)
    if a is None or b is None:
        return None
    return abs(a - b)


def math_diff_pct(val_a: Any, val_b: Any, options: dict | None = None) -> float | None:
    a, b = _to_float(val_a), _to_float(val_b)
    if a is None or b is None:
        return None
    if a == 0:
        return None if b == 0 else math.inf
    return ((a - b) / a) * 100.0


def math_tolerance(val_a: Any, val_b: Any, options: dict | None = None) -> dict[str, Any]:
    options = options or {}
    a, b = _to_float(val_a), _to_float(val_b)
    if a is None or b is None:
        return {
            "is_within_tolerance": False,
            "delta_abs": None,
            "delta_pct": None,
        }

    delta_abs = abs(a - b)
    epsilon = options.get("epsilon")
    tolerance_pct = options.get("tolerance_pct")

    within = True
    if epsilon is not None:
        within = within and delta_abs <= float(epsilon)
    if tolerance_pct is not None and a != 0:
        delta_pct = abs((a - b) / a) * 100.0
        within = within and delta_pct <= float(tolerance_pct)
    elif tolerance_pct is not None and a == 0:
        within = within and b == 0

    return {
        "is_within_tolerance": within,
        "delta_abs": delta_abs,
        "delta_pct": None if a == 0 else abs((a - b) / a) * 100.0,
    }


def math_ratio(val_a: Any, val_b: Any, options: dict | None = None) -> float | None:
    a, b = _to_float(val_a), _to_float(val_b)
    if a is None or b is None or b == 0:
        return None
    return a / b


# --- B. Texto y Cadenas ---


def strict_equal(val_a: Any, val_b: Any, options: dict | None = None) -> bool:
    return val_a == val_b


def normalized_equal(val_a: Any, val_b: Any, options: dict | None = None) -> bool:
    return _normalize_text(val_a) == _normalize_text(val_b)


def fuzzy_levenshtein(val_a: Any, val_b: Any, options: dict | None = None) -> float:
    a, b = _to_str(val_a), _to_str(val_b)
    if not a and not b:
        return 1.0
    distance = _levenshtein_distance(a, b)
    return 1.0 - (distance / max(len(a), len(b), 1))


def fuzzy_jaro_winkler(val_a: Any, val_b: Any, options: dict | None = None) -> float:
    return _jaro_winkler_similarity(_to_str(val_a), _to_str(val_b))


def contains_check(val_a: Any, val_b: Any, options: dict | None = None) -> bool:
    a, b = _to_str(val_a), _to_str(val_b)
    return a in b or b in a


def regex_match(val_a: Any, val_b: Any, options: dict | None = None) -> bool:
    options = options or {}
    pattern = options.get("pattern", val_b)
    flags = re.IGNORECASE if options.get("ignore_case", False) else 0
    return re.search(str(pattern), _to_str(val_a), flags) is not None


# --- C. Fechas (delegación a datetime_parser) ---


def op_date_diff_seconds(val_a: Any, val_b: Any, options: dict | None = None) -> float:
    return date_diff_seconds(val_a, val_b)


def op_date_diff_days(val_a: Any, val_b: Any, options: dict | None = None) -> float:
    return date_diff_days(val_a, val_b)


def op_date_equal(val_a: Any, val_b: Any, options: dict | None = None) -> bool:
    return date_equal(val_a, val_b)


def op_date_tolerance(val_a: Any, val_b: Any, options: dict | None = None) -> dict[str, float | bool]:
    options = options or {}
    tolerance = float(options.get("tolerance_seconds", 0))
    return date_tolerance(val_a, val_b, tolerance)


# --- D. Listas y Arreglos ---


def _serialize_item(item: Any) -> str:
    if isinstance(item, (dict, list)):
        return json.dumps(item, sort_keys=True)
    return str(item)


def array_intersection(val_a: Any, val_b: Any, options: dict | None = None) -> list[Any]:
    list_a = _to_list(val_a)
    keys_b = {_serialize_item(item) for item in _to_list(val_b)}
    seen: set[str] = set()
    result: list[Any] = []
    for item in list_a:
        key = _serialize_item(item)
        if key in keys_b and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def array_difference(val_a: Any, val_b: Any, options: dict | None = None) -> list[Any]:
    list_b = _to_list(val_b)
    result: list[Any] = []
    for item in _to_list(val_a):
        if item not in list_b and item not in result:
            result.append(item)
    return result


def array_jaccard(val_a: Any, val_b: Any, options: dict | None = None) -> float:
    set_a = set(_to_list(val_a))
    set_b = set(_to_list(val_b))
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


# --- E. Lógica y Estructura ---


def null_check(val_a: Any, val_b: Any, options: dict | None = None) -> dict[str, bool]:
    return {
        "a_is_null": val_a is None,
        "b_is_null": val_b is None,
        "both_null": val_a is None and val_b is None,
        "any_null": val_a is None or val_b is None,
    }


def boolean_logic(val_a: Any, val_b: Any, options: dict | None = None) -> bool:
    options = options or {}
    operator = str(options.get("operator", "AND")).upper()
    a, b = _to_bool(val_a), _to_bool(val_b)

    if operator == "OR":
        return a or b
    if operator == "XOR":
        return a ^ b
    return a and b


OPERATIONS_REGISTRY: dict[str, OperationFn] = {
    # A. Numéricas
    "math_add": math_add,
    "math_sub": math_sub,
    "math_diff_abs": math_diff_abs,
    "math_diff_pct": math_diff_pct,
    "math_tolerance": math_tolerance,
    "math_ratio": math_ratio,
    # B. Texto
    "strict_equal": strict_equal,
    "normalized_equal": normalized_equal,
    "fuzzy_levenshtein": fuzzy_levenshtein,
    "fuzzy_jaro_winkler": fuzzy_jaro_winkler,
    "contains_check": contains_check,
    "regex_match": regex_match,
    # C. Fechas
    "date_diff_seconds": op_date_diff_seconds,
    "date_diff_days": op_date_diff_days,
    "date_equal": op_date_equal,
    "date_tolerance": op_date_tolerance,
    # D. Arrays
    "array_intersection": array_intersection,
    "array_difference": array_difference,
    "array_jaccard": array_jaccard,
    # E. Lógica
    "null_check": null_check,
    "boolean_logic": boolean_logic,
}

# Export auxiliar para tests / introspección
def parse_datetime_preview(value: Any) -> datetime:
    return parse_to_utc_datetime(value)
