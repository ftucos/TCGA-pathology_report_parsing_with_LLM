"""Four report-level findings, without CAP fields or evidence requirements."""

import json

from pydantic import BaseModel, ConfigDict, field_validator

SCHEMA_VERSION = "2.0"


class BladderExtraction(BaseModel):
    # Guide generation toward four fields, but tolerate extra keys when parsing.
    model_config = ConfigDict(extra="ignore", json_schema_extra={"additionalProperties": False})

    stage: str | None = None
    grade: str | None = None
    histology: str | None = None
    margins: str | None = None

    @field_validator("stage", "grade", "histology", "margins", mode="before")
    @classmethod
    def readable_value(cls, value):
        """Keep unexpected JSON values as text rather than reject a useful report."""
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if value == [] or value == {}:
            return None
        return json.dumps(value, ensure_ascii=False)
