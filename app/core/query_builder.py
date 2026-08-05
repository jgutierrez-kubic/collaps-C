import logging
import re

from app.models.payload import AnalysisPayload, _IDENTIFIER_RE, sanitize_table_identifier

logger = logging.getLogger(__name__)

__all__ = [
    "build_analysis_sql",
    "log_join_uniqueness_warning",
    "sanitize_column_part",
    "sanitize_table_identifier",
    "split_csv",
    "sql_source_column_alias",
]

def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def sanitize_column_part(part: str) -> str:
    """Sanitiza una parte de nombre de columna SQL (alineado con AnalysisEngine)."""
    clean = str(part).strip().lower().replace("/", "_").replace(" ", "_")
    clean = re.sub(r"[^a-z0-9_]", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean:
        raise ValueError(f"No se pudo sanitizar la parte de columna: '{part}'")
    return clean


def sql_source_column_alias(pair_index: int, col_name: str, side: str) -> str:
    """Alias indexado emitido por build_analysis_sql: '{index}_{col}_{side}'."""
    clean = sanitize_column_part(col_name)
    return f"{pair_index}_{clean}_{side.lower()}"


_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _quote_ident(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Identificador SQL inválido: '{name}'")
    return f'"{name}"'


def _quote_alias(name: str) -> str:
    """Cita alias de columna (permite prefijo numérico indexado, ej. 0_val_a)."""
    if not _ALIAS_RE.match(name):
        raise ValueError(f"Alias SQL inválido: '{name}'")
    return f'"{name}"'


def log_join_uniqueness_warning(
    payload: AnalysisPayload,
    source_stats: dict[str, int] | None = None,
) -> None:
    """Registra advertencia si las llaves de cruce pueden producir duplicados en el JOIN.

    FULL OUTER JOIN con llaves no únicas en tabla_a o tabla_b genera un producto
    cartesiano parcial: cada combinación de filas con la misma llave produce una
    fila Match adicional en el resultado.
    """
    base_msg = (
        "FULL OUTER JOIN entre "
        f"{payload.schema_name}.{payload.table_a} y {payload.schema_name}.{payload.table_b} "
        f"usando {payload.join_key_a} = {payload.join_key_b}. "
        "Si las llaves de cruce no son únicas en los orígenes, el JOIN multiplicará "
        "filas Match (producto cartesiano parcial)."
    )

    if source_stats is None:
        logger.info("⚠️ [QUERY BUILDER] %s", base_msg)
        return

    total_a = source_stats["total_a"]
    unique_a = source_stats["unique_a"]
    total_b = source_stats["total_b"]
    unique_b = source_stats["unique_b"]

    if total_a > unique_a or total_b > unique_b:
        logger.warning(
            "⚠️ [QUERY BUILDER] %s "
            "Detectado: tabla_a %d filas / %d llaves únicas, tabla_b %d filas / %d llaves únicas.",
            base_msg,
            total_a,
            unique_a,
            total_b,
            unique_b,
        )
    else:
        logger.info(
            "✅ [QUERY BUILDER] Llaves de cruce únicas en orígenes "
            "(tabla_a: %d/%d, tabla_b: %d/%d).",
            unique_a,
            total_a,
            unique_b,
            total_b,
        )


def build_analysis_sql(payload: AnalysisPayload) -> str:
    """Construye la consulta SQL de cruce (JOIN + selección de columnas base).

    Los cálculos definidos en metodos_calculo se aplican en Python vía collaps_engine.

    Nota técnica: un FULL OUTER JOIN sobre llaves no únicas produce filas Match
  duplicadas. Use log_join_uniqueness_warning() para auditar los orígenes.
    """
    columnas_a = split_csv(payload.columns_a)
    columnas_b = split_csv(payload.columns_b)
    metodos = split_csv(payload.calculation_methods)

    if len(columnas_a) != len(columnas_b) or len(columnas_a) != len(metodos):
        raise ValueError(
            "columns_a, columns_b and calculation_methods must have the same number of elements."
        )

    schema = _quote_ident(payload.schema_name)
    tabla_a = _quote_ident(payload.table_a)
    tabla_b = _quote_ident(payload.table_b)
    llave_a = _quote_ident(payload.join_key_a)
    llave_b = _quote_ident(payload.join_key_b)

    select_parts = [
        f"COALESCE(a.{llave_a}::text, b.{llave_b}::text) AS {_quote_ident('llave_cruce')}",
        f"a.{llave_a} AS {_quote_ident(f'{payload.join_key_a}_a')}",
        f"b.{llave_b} AS {_quote_ident(f'{payload.join_key_b}_b')}",
        (
            "CASE "
            f"WHEN a.{llave_a} IS NOT NULL AND b.{llave_b} IS NOT NULL THEN 'Match' "
            f"WHEN a.{llave_a} IS NOT NULL THEN 'Only A' "
            "ELSE 'Only B' "
            "END AS estado_cruce"
        ),
    ]

    for pair_index, (col_a, col_b) in enumerate(zip(columnas_a, columnas_b)):
        alias_a = sql_source_column_alias(pair_index, col_a, "a")
        alias_b = sql_source_column_alias(pair_index, col_b, "b")
        select_parts.append(f"a.{_quote_ident(col_a)} AS {_quote_alias(alias_a)}")
        select_parts.append(f"b.{_quote_ident(col_b)} AS {_quote_alias(alias_b)}")

    select_clause = ",\n    ".join(select_parts)

    return (
        f"SELECT\n    {select_clause}\n"
        f"FROM {schema}.{tabla_a} a\n"
        f"FULL OUTER JOIN {schema}.{tabla_b} b\n"
        f"    ON a.{llave_a} = b.{llave_b}"
    )
