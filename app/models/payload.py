import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from collaps_engine.comparison_engine import OPERATIONS_REGISTRY

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DEFAULT_SCHEMA_NAME = "s00001_incancer"
LEGACY_METHOD_ALIASES = {"DIFERENCIA", "IGUALDAD"}
ALLOWED_CALCULATION_METHODS = set(OPERATIONS_REGISTRY.keys()) | LEGACY_METHOD_ALIASES


def sanitize_table_identifier(value: str) -> str:
    """Extracts the bare table name and validates it as a safe SQL identifier."""
    clean = value.strip()
    if "." in clean:
        clean = clean.rsplit(".", 1)[-1].strip()

    if not _IDENTIFIER_RE.match(clean):
        raise ValueError(f"Invalid SQL identifier: '{value}'")

    return clean


class AnalysisPayload(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        strict=True,
        extra="forbid",
    )

    source: Literal["directus", "n8n"] = "directus"
    analysis_id: Optional[str] = None
    schema_name: str = DEFAULT_SCHEMA_NAME
    analysis_name: Optional[str] = None
    table_a: str
    table_b: str
    join_key_a: str
    join_key_b: str
    columns_a: str = Field(description='Example: "quantity" or "quantity, price"')
    columns_b: str = Field(description='Example: "quantity" or "quantity, price"')
    calculation_methods: str = Field(
        description=(
            "collaps_engine method_ids comma-separated. "
            'E.g. "math_sub, strict_equal" or legacy "DIFERENCIA, IGUALDAD"'
        )
    )
    target_table: str
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

    @field_validator("table_a", "table_b", "target_table", mode="before")
    @classmethod
    def sanitize_qualified_table_names(cls, value: object) -> str:
        if value is None:
            raise ValueError("Table name is required.")
        return sanitize_table_identifier(str(value))

    @field_validator("join_key_a", "join_key_b")
    @classmethod
    def validate_join_keys(cls, value: str) -> str:
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(f"Invalid SQL identifier: '{value}'")
        return value

    @field_validator("columns_a", "columns_b")
    @classmethod
    def validate_column_lists(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("Field cannot be empty.")
        for item in items:
            if not _IDENTIFIER_RE.match(item):
                raise ValueError(f"Invalid SQL identifier: '{item}'")
        return value

    @field_validator("calculation_methods")
    @classmethod
    def validate_methods(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("calculation_methods cannot be empty.")

        invalid: list[str] = []
        for item in items:
            normalized = item.upper() if item.upper() in LEGACY_METHOD_ALIASES else item.lower()
            if normalized not in ALLOWED_CALCULATION_METHODS:
                invalid.append(item)

        if invalid:
            raise ValueError(
                f"Unsupported methods: {', '.join(sorted(invalid))}. "
                "Use a collaps_engine method_id or legacy DIFERENCIA/IGUALDAD."
            )
        return value

    def qualified_table_a(self) -> str:
        return f"{self.schema_name}.{self.table_a}"

    def qualified_table_b(self) -> str:
        return f"{self.schema_name}.{self.table_b}"

    def qualified_target_table(self) -> str:
        return f"{self.schema_name}.{self.target_table}"
