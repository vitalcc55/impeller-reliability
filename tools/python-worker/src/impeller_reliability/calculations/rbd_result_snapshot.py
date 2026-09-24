from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RbdExactRationalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    numerator: str = Field(pattern=r"^-?(?:0|[1-9][0-9]{0,63})$")
    denominator: str = Field(pattern=r"^[1-9][0-9]{0,63}$")
    decimal: str | None = Field(max_length=128)
    decimal_preview: str = Field(min_length=1, max_length=128)


class RbdPhaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    phase: Literal["acceleration", "steady_rotation", "deceleration"]
    start_s: RbdExactRationalResult
    end_s: RbdExactRationalResult
    start_rpm: RbdExactRationalResult
    end_rpm: RbdExactRationalResult


class RbdDiagramPointResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    boundary: Literal["start", "acceleration_end", "steady_end", "cycle_end"]
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=100)


class RbdFailureResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["calculated", "not_applicable"]
    cycles_to_failure: str | None = Field(pattern=r"^(?:0|[1-9][0-9]{0,63})$")
    reason_code: str | None = Field(max_length=100)


class RbdReferenceResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    algorithm_id: Literal["rbd_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    maximum_rpm: RbdExactRationalResult
    required_cycles_exact: RbdExactRationalResult
    steady_duration_s_exact: RbdExactRationalResult
    cycle_duration_s_exact: RbdExactRationalResult
    total_duration_s_exact: RbdExactRationalResult
    failure_result: RbdFailureResultModel
    phases: list[RbdPhaseResult] = Field(min_length=3, max_length=3)
    diagram_points: list[RbdDiagramPointResult] = Field(min_length=4, max_length=4)
    formula_references: list[str] = Field(min_length=3, max_length=8)

    @model_validator(mode="after")
    def validate_ordered_profile(self) -> RbdReferenceResultModel:
        if [phase.phase for phase in self.phases] != ["acceleration", "steady_rotation", "deceleration"]:
            raise ValueError("rbd_phase_order_invalid")
        if [point.boundary for point in self.diagram_points] != [
            "start",
            "acceleration_end",
            "steady_end",
            "cycle_end",
        ]:
            raise ValueError("rbd_diagram_order_invalid")
        return self
