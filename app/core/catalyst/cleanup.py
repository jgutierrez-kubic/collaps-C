"""Normalización técnica dual: valor_original y valor_limpio (RMS Genérico v1.4)."""

from __future__ import annotations

import json
import re
from typing import Any

NUMERIC_TYPES = frozenset({"numero", "superficie"})


def to_valor_original(raw_value: Any) -> str:
    """Conserva el string crudo entregado por el consultor."""
    if raw_value is None:
        return ""
    return str(raw_value)


def normalize_numeric_string(text: str) -> str:
    """Extrae dígitos ASCII, puntos y signos; convierte comas en puntos."""
    converted = text.replace(",", ".")
    return "".join(char for char in converted if char in "0123456789.-+")


def normalize_text_string(text: str) -> str:
    """Trim y colapso de espacios múltiples."""
    return re.sub(r"\s+", " ", text).strip()


def to_valor_limpio(raw_value: Any, tipo_dato_generico: str) -> str:
    """Normaliza el valor según tipo_dato_generico."""
    original = to_valor_original(raw_value)
    tipo = tipo_dato_generico.strip().lower()

    if tipo in NUMERIC_TYPES:
        return normalize_numeric_string(original)

    return normalize_text_string(original)


def canonical_string(value: Any) -> str:
    """Representación estable para calcular firma_auditoria."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)
