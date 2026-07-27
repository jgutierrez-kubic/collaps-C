"""Normalización y comparación de marcas de tiempo hacia UTC."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

_ISO_Z_SUFFIX = re.compile(r"Z$", re.IGNORECASE)
_STANDARD_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$"
)


def parse_to_utc_datetime(value: Any) -> datetime:
    """Convierte entradas heterogéneas a datetime con zona horaria UTC explícita."""
    if value is None:
        raise ValueError("No se puede parsear un valor nulo a datetime.")

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 1_000_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("Cadena de fecha vacía.")

        if raw.isdigit():
            return parse_to_utc_datetime(int(raw))

        normalized = _ISO_Z_SUFFIX.sub("+00:00", raw)
        if _STANDARD_DATETIME.match(raw):
            normalized = raw.replace(" ", "T") + "+00:00"

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"Formato de fecha no soportado: '{value}'") from exc

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    raise TypeError(f"Tipo no soportado para parseo de fecha: {type(value).__name__}")


def date_diff_seconds(val_a: Any, val_b: Any) -> float:
    """Diferencia absoluta en segundos entre dos valores de fecha."""
    dt_a = parse_to_utc_datetime(val_a)
    dt_b = parse_to_utc_datetime(val_b)
    return abs((dt_a - dt_b).total_seconds())


def date_diff_days(val_a: Any, val_b: Any) -> float:
    """Diferencia absoluta en días entre dos valores de fecha."""
    return date_diff_seconds(val_a, val_b) / 86_400.0


def date_equal(val_a: Any, val_b: Any) -> bool:
    """Compara si dos valores corresponden al mismo día calendario (UTC)."""
    dt_a = parse_to_utc_datetime(val_a)
    dt_b = parse_to_utc_datetime(val_b)
    return dt_a.date() == dt_b.date()


def date_tolerance(val_a: Any, val_b: Any, tolerance_seconds: float) -> dict[str, float | bool]:
    """Evalúa si dos fechas están dentro de una tolerancia en segundos."""
    delta = date_diff_seconds(val_a, val_b)
    return {
        "is_within_tolerance": delta <= tolerance_seconds,
        "delta_seconds": delta,
    }
