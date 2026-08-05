"""Motor híbrido Polars + UDF collaps_engine para transformaciones por chunk."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import polars as pl

from app.core.query_builder import split_csv, sql_source_column_alias
from collaps_engine.comparison_engine import _normalize_text, _to_bool
from collaps_engine.transformer import execute_transformation

if TYPE_CHECKING:
    from app.core.analysis_engine import AnalysisEngine

logger = logging.getLogger(__name__)

_BOOLEAN_PURE_METHODS: frozenset[str] = frozenset({
    "strict_equal",
    "normalized_equal",
    "date_equal",
    "regex_match",
    "null_check",
    "boolean_logic",
    "contains_check",
})

_VECTORIZED_METHOD_IDS: frozenset[str] = frozenset({
    "math_add",
    "math_sub",
    "math_diff_abs",
    "math_diff_pct",
    "math_ratio",
    "strict_equal",
    "normalized_equal",
    "date_equal",
    "boolean_logic",
})


def _operand_exprs(
    col_a_key: str,
    col_b_key: str,
    swap_operands: bool,
) -> tuple[pl.Expr, pl.Expr]:
    expr_a = pl.col(col_a_key)
    expr_b = pl.col(col_b_key)
    if swap_operands:
        return expr_b, expr_a
    return expr_a, expr_b


def _as_float(expr: pl.Expr) -> pl.Expr:
    return expr.cast(pl.Float64, strict=False)


def _vectorized_result_expr(
    method_id: str,
    col_a_key: str,
    col_b_key: str,
    swap_operands: bool,
) -> pl.Expr | None:
    expr_a, expr_b = _operand_exprs(col_a_key, col_b_key, swap_operands)

    if method_id == "math_add":
        return _as_float(expr_a) + _as_float(expr_b)

    if method_id == "math_sub":
        return _as_float(expr_a) - _as_float(expr_b)

    if method_id == "math_diff_abs":
        return (_as_float(expr_a) - _as_float(expr_b)).abs()

    if method_id == "math_diff_pct":
        a_f = _as_float(expr_a)
        b_f = _as_float(expr_b)
        valid = a_f.is_not_null() & b_f.is_not_null()
        return (
            pl.when(valid & (a_f != 0))
            .then((a_f - b_f) / a_f * 100.0)
            .when(valid & (a_f == 0) & (b_f != 0))
            .then(float("inf"))
            .otherwise(None)
        )

    if method_id == "math_ratio":
        a_f = _as_float(expr_a)
        b_f = _as_float(expr_b)
        valid = a_f.is_not_null() & b_f.is_not_null() & (b_f != 0)
        return pl.when(valid).then(a_f / b_f).otherwise(None)

    if method_id == "strict_equal":
        return expr_a == expr_b

    if method_id == "normalized_equal":
        a_norm = expr_a.map_elements(_normalize_text, return_dtype=pl.Utf8)
        b_norm = expr_b.map_elements(_normalize_text, return_dtype=pl.Utf8)
        return a_norm == b_norm

    if method_id == "date_equal":
        dt_a = expr_a.cast(pl.Datetime(time_zone="UTC"), strict=False).dt.date()
        dt_b = expr_b.cast(pl.Datetime(time_zone="UTC"), strict=False).dt.date()
        return dt_a == dt_b

    if method_id == "boolean_logic":
        a_bool = expr_a.map_elements(_to_bool, return_dtype=pl.Boolean)
        b_bool = expr_b.map_elements(_to_bool, return_dtype=pl.Boolean)
        return a_bool & b_bool

    return None


def _udf_row_result(
    val_a: Any,
    val_b: Any,
    method_id: str,
    swap_operands: bool,
) -> dict[str, Any]:
    if swap_operands:
        val_a, val_b = val_b, val_a
    return execute_transformation(val_a, val_b, method_id)


def _apply_udf_pair(
    col_a_key: str,
    col_b_key: str,
    method_id: str,
    swap_operands: bool,
) -> tuple[pl.Expr, pl.Expr]:
    def row_fn(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            val_a = row[col_a_key]
            val_b = row[col_b_key]
        else:
            val_a, val_b = row[0], row[1]
        return _udf_row_result(val_a, val_b, method_id, swap_operands)

    udf_results = pl.struct([
        pl.col(col_a_key).alias(col_a_key),
        pl.col(col_b_key).alias(col_b_key),
    ]).map_elements(row_fn, return_dtype=pl.Object)

    result_expr = udf_results.map_elements(
        lambda item: item.get("result_value") if isinstance(item, dict) else None,
        return_dtype=pl.Object,
    )
    match_expr = udf_results.map_elements(
        lambda item: item.get("is_match") if isinstance(item, dict) else None,
        return_dtype=pl.Object,
    )
    return result_expr, match_expr


def transform_chunk_with_polars(df_pandas, engine: AnalysisEngine):
    """Convierte el chunk a Polars, transforma y devuelve Pandas para persistencia."""
    import pandas as pd

    pl_df = pl.from_pandas(df_pandas)
    columnas_a = split_csv(engine.payload.columns_a)
    columnas_b = split_csv(engine.payload.columns_b)
    metodos = split_csv(engine.payload.calculation_methods)

    logger.info(
        "⚙️ [POLARS - COLLAPS] Aplicando %d transformación(es) vía motor híbrido...",
        len(metodos),
    )

    new_columns: list[pl.Expr] = []
    drop_keys: list[str] = []

    for pair_index, (col_a, col_b, method_raw) in enumerate(
        zip(columnas_a, columnas_b, metodos)
    ):
        method_id, swap_operands, _method_label = engine._resolve_method(method_raw)
        col_a_key = sql_source_column_alias(pair_index, col_a, "a")
        col_b_key = sql_source_column_alias(pair_index, col_b, "b")

        if col_a_key not in pl_df.columns or col_b_key not in pl_df.columns:
            raise KeyError(
                f"Columnas requeridas no encontradas en el resultado SQL: "
                f"'{col_a_key}' / '{col_b_key}'"
            )

        vectorized = _vectorized_result_expr(method_id, col_a_key, col_b_key, swap_operands)
        match_expr: pl.Expr | None = None

        if vectorized is not None:
            result_expr = vectorized
            logger.debug(
                "⚙️ [POLARS - COLLAPS] Par %d — método '%s' vectorizado",
                pair_index,
                method_id,
            )
        else:
            result_expr, match_expr = _apply_udf_pair(
                col_a_key,
                col_b_key,
                method_id,
                swap_operands,
            )

        indexed_a = engine._indexed_source_column_name(pair_index, col_a, "A")
        indexed_b = engine._indexed_source_column_name(pair_index, col_b, "B")
        indexed_method = engine._indexed_column_name(pair_index, "metodo_aplicado")
        out_col = engine._indexed_column_name(
            pair_index,
            engine._result_suffix(method_raw, method_id),
        )

        new_columns.extend([
            pl.col(col_a_key).alias(indexed_a),
            pl.col(col_b_key).alias(indexed_b),
            pl.lit(method_raw.strip()).alias(indexed_method),
            result_expr.alias(out_col),
        ])

        is_pure_boolean = method_id in _BOOLEAN_PURE_METHODS
        if not is_pure_boolean and match_expr is not None:
            match_col = engine._indexed_column_name(pair_index, "is_match")
            new_columns.append(match_expr.alias(match_col))

        drop_keys.extend([col_a_key, col_b_key])

        logger.info(
            "⚙️ [POLARS - COLLAPS] Par %d — método '%s' → columnas '%s', '%s', '%s'",
            pair_index,
            method_id,
            indexed_a,
            indexed_b,
            out_col,
        )

    pl_df = pl_df.with_columns(new_columns)
    existing_drop = [col for col in drop_keys if col in pl_df.columns]
    if existing_drop:
        pl_df = pl_df.drop(existing_drop)

    return pl_df.to_pandas(use_pyarrow_extension_array=False)
