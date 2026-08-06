"""Normalización técnica dual: valor_original y valor_limpio (RMS Genérico v1.4)."""

from __future__ import annotations

import json
import math
import re
from typing import Any

NUMERIC_TYPES = frozenset({
    "numero",
    "superficie",
    "largo",
    "moneda",
    "porcentaje",
    "peso",
})


def is_empty_source_value(raw_value: Any) -> bool:
    """Detecta valores vacíos del origen (None, NaN, string en blanco)."""
    if raw_value is None:
        return True
    if isinstance(raw_value, float) and math.isnan(raw_value):
        return True
    if isinstance(raw_value, str) and not raw_value.strip():
        return True
    return False


def to_valor_original(raw_value: Any) -> str | None:
    """Conserva el string crudo; None si el origen viene vacío."""
    if is_empty_source_value(raw_value):
        return None
    return str(raw_value)


def normalize_numeric_string(text: str) -> str:
    """Extrae dígitos ASCII, puntos y signos; convierte comas en puntos."""
    converted = text.replace(",", ".")
    return "".join(char for char in converted if char in "0123456789.-+")


def normalize_text_string(text: str) -> str:
    """Trim y colapso de espacios múltiples."""
    return re.sub(r"\s+", " ", text).strip()


def to_valor_limpio(raw_value: Any, tipo_dato_generico: str) -> str | None:
    """Normaliza el valor según tipo_dato_generico; None si el origen está vacío."""
    tipo = tipo_dato_generico.strip().lower()

    if tipo == "internal_id":
        if is_empty_source_value(raw_value):
            raise ValueError("internal_id no puede ser nulo ni vacío.")
        cleaned = normalize_text_string(str(raw_value))
        if not cleaned:
            raise ValueError("internal_id no puede ser nulo ni vacío.")
        return cleaned

    if is_empty_source_value(raw_value):
        return None

    original = str(raw_value)

    if tipo in NUMERIC_TYPES:
        return normalize_numeric_string(original) or None

    return normalize_text_string(original)


def canonical_string(value: Any) -> str:
    """Representación estable para calcular firma_auditoria."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)
