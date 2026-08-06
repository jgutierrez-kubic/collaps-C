"""Traducción canónica D12 — estructura de 5 casillas para rol requisito."""

from __future__ import annotations

from typing import Any

from app.core.catalyst.models import ConfigRow


def build_cinco_casillas(config: ConfigRow, valor_limpio: Any) -> dict[str, Any]:
    """Prepara la estructura de 5 casillas para el motor de cruce COLLAPS."""
    return {
        "casilla_1_clave": config.propiedad,
        "casilla_2_valor": valor_limpio,
        "casilla_3_unidad": config.unidad_esperada or None,
        "casilla_4_formato": config.formato_entrada,
        "casilla_5_parametro": config.parametro or None,
    }
