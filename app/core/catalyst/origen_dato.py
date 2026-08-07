"""Orígenes de dato RMS — valores canónicos para trazabilidad en bóveda."""

from __future__ import annotations

ORIGEN_CARGA_MASIVA = "carga_masiva"
ORIGEN_EDICION_MANUAL = "edicion_manual"
ORIGEN_INFERENCIA_SISTEMA = "inferencia_sistema"

ORIGENES_DATO = frozenset({
    ORIGEN_CARGA_MASIVA,
    ORIGEN_EDICION_MANUAL,
    ORIGEN_INFERENCIA_SISTEMA,
})
