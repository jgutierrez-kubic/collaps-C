import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.models.payload import DEFAULT_SCHEMA_NAME, _IDENTIFIER_RE, sanitize_table_identifier

_ORDER_BY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\s+(ASC|DESC|asc|desc)$")


class WorktableCreatePayload(BaseModel):
    """Payload to create a materialized worktable from a source table."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        strict=True,
        extra="forbid",
    )

    schema_name: str = DEFAULT_SCHEMA_NAME
    source_table: str = Field(description="PostgreSQL source table (pre-formatted from n8n)")
    target_table: str = Field(
        description="Physical target table name (e.g. c_results_precioFrutas)"
    )
    group_by_columns: str = Field(
        description='Group-by columns comma-separated. E.g. "category, region"'
    )
    order_by_rules: str = Field(
        description='ORDER BY rules comma-separated. E.g. "total DESC, name ASC"'
    )
    callback_url: Optional[str] = None

    @field_validator("schema_name", mode="before")
    @classmethod
    def normalize_schema_name(cls, value: object) -> str:
        if value is None:
            return DEFAULT_SCHEMA_NAME
        if isinstance(value, str) and not value.strip():
            return DEFAULT_SCHEMA_NAME
        return str(value).strip()

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, value: str) -> str:
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(f"Invalid SQL identifier for schema_name: '{value}'")
        return value

    @field_validator("source_table", "target_table", mode="before")
    @classmethod
    def sanitize_table_names(cls, value: object) -> str:
        if value is None:
            raise ValueError("Table name is required.")
        return sanitize_table_identifier(str(value))

    @field_validator("group_by_columns")
    @classmethod
    def validate_group_by_columns(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("group_by_columns cannot be empty.")
        for item in items:
            if not _IDENTIFIER_RE.match(item):
                raise ValueError(f"Invalid SQL identifier: '{item}'")
        return value

    @field_validator("order_by_rules")
    @classmethod
    def validate_order_by_rules(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("order_by_rules cannot be empty.")
        for item in items:
            if not _ORDER_BY_RE.match(item):
                raise ValueError(
                    f"Invalid ORDER BY rule: '{item}'. Use format 'column ASC' or 'column DESC'."
                )
        return value
