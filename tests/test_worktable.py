"""Tests for worktable payload and skeleton."""

from __future__ import annotations

import pytest

from app.models.worktable_payload import WorktableCreatePayload


def test_worktable_payload_valid_camel_case() -> None:
    payload = WorktableCreatePayload.model_validate(
        {
            "sourceTable": "c_source_data",
            "targetTable": "c_results_precioFrutas",
            "groupByColumns": "categoria, region",
            "orderByRules": "categoria ASC, total DESC",
        }
    )
    assert payload.source_table == "c_source_data"
    assert payload.target_table == "c_results_precioFrutas"
    assert payload.group_by_columns == "categoria, region"


def test_worktable_payload_rejects_invalid_order_by() -> None:
    with pytest.raises(ValueError, match="Invalid ORDER BY rule"):
        WorktableCreatePayload.model_validate(
            {
                "sourceTable": "c_source",
                "targetTable": "c_target",
                "groupByColumns": "categoria",
                "orderByRules": "DROP TABLE users",
            }
        )
