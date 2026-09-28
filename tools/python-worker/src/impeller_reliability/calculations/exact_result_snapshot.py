from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExactRationalResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    numerator: str = Field(pattern=r"^-?(?:0|[1-9][0-9]{0,63})$")
    denominator: str = Field(pattern=r"^[1-9][0-9]{0,63}$")
    decimal: str | None = Field(max_length=128)
    decimal_preview: str = Field(min_length=1, max_length=128)
