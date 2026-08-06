"""Limpieza y normalización de valores según formato_entrada y regla_limpieza."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.catalyst.models import ConfigRow

logger = logging.getLogger(__name__)


def _apply_regla_limpieza(value: str, regla: str) -> str:
    if not regla:
        return value

    rule = regla.strip().lower()
    if rule in {"trim", "espacios"}:
        return value.strip()
    if rule in {"upper", "mayusculas"}:
        return value.upper()
    if rule in {"lower", "minusculas"}:
        return value.lower()
    if rule in {"sin_espacios", "no_spaces"}:
        return value.replace(" ", "")

    logger.debug("Regla de limpieza no implementada localmente: %s — se conserva valor", regla)
    return value


def _coerce_formato(value: Any, formato: str) -> Any:
    if value is None:
        return None

    fmt = formato.strip().lower()
    if fmt == "numero":
        try:
            return float(str(value).replace(",", ".").strip())
        except ValueError:
            return None
    if fmt == "si_no":
        text = str(value).strip().lower()
        return text in {"1", "true", "t", "yes", "y", "si", "sí", "s"}
    if fmt == "lista":
        if isinstance(value, list):
            return value
        text = str(value).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    return str(value).strip()


def clean_value(raw_value: Any, config: ConfigRow) -> Any:
    """Aplica formato_entrada y regla_limpieza a un valor de celda."""
    coerced = _coerce_formato(raw_value, config.formato_entrada)
    if coerced is None:
        return None
    if isinstance(coerced, str):
        return _apply_regla_limpieza(coerced, config.regla_limpieza)
    return coerced


def canonical_string(value: Any) -> str:
    """Representación estable del valor para calcular firma_valor (D11)."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)
