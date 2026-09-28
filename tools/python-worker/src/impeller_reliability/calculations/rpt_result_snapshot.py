from __future__ import annotations

from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from impeller_reliability.calculations.exact_result_snapshot import ExactRationalResultModel


class RptPhaseResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    phase: Literal["acceleration", "steady_rotation", "deceleration"]
    start_s: ExactRationalResultModel
    end_s: ExactRationalResultModel
    start_rpm: ExactRationalResultModel
    end_rpm: ExactRationalResultModel


class RptDiagramPointResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    boundary: Literal[
        "cycle_start",
        "acceleration_end",
        "steady_end",
        "cycle_end",
        "repeat_acceleration_end",
        "repeat_steady_end",
        "repeat_cycle_end",
    ]
    x: int = Field(ge=0, le=2000)
    y: int = Field(ge=0, le=100)


class RptFailureResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["calculated", "not_applicable"]
    cycles_to_failure: str | None = Field(pattern=r"^(?:0|[1-9][0-9]{0,63})$")
    reason_code: str | None = Field(max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> RptFailureResultModel:
        if self.status == "calculated":
            if self.cycles_to_failure is None or self.reason_code is not None:
                raise ValueError("rpt_failure_result_invalid")
        elif self.cycles_to_failure is not None or self.reason_code is None:
            raise ValueError("rpt_failure_result_invalid")
        return self


class RptLowerPointComparisonModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_policy: Literal["one_percent", "full_stop", "explicit_rpm"]
    target_policy: Literal["one_percent", "full_stop", "explicit_rpm"]
    source_explicit_lower_rpm: str | None = Field(max_length=512)
    target_lower_rpm: str = Field(min_length=1, max_length=512)
    status: Literal[
        "matches_typical_formula",
        "differs_from_typical_formula",
        "source_target_policy_conflict",
        "target_unavailable",
    ]


class RptReferenceResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    algorithm_id: Literal["rpt_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    maximum_rpm: ExactRationalResultModel
    minimum_rpm: ExactRationalResultModel
    required_cycles_exact: ExactRationalResultModel
    cycle_duration_s_exact: ExactRationalResultModel
    total_duration_s_exact: ExactRationalResultModel
    total_duration_h_exact: ExactRationalResultModel
    failure_result: RptFailureResultModel
    lower_point_comparison: RptLowerPointComparisonModel
    phases: list[RptPhaseResultModel] = Field(min_length=3, max_length=3)
    diagram_points: list[RptDiagramPointResultModel] = Field(min_length=7, max_length=7)
    formula_references: list[str] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_ordered_profile(self) -> RptReferenceResultModel:
        if [phase.phase for phase in self.phases] != ["acceleration", "steady_rotation", "deceleration"]:
            raise ValueError("rpt_phase_order_invalid")
        if [point.boundary for point in self.diagram_points] != [
            "cycle_start",
            "acceleration_end",
            "steady_end",
            "cycle_end",
            "repeat_acceleration_end",
            "repeat_steady_end",
            "repeat_cycle_end",
        ]:
            raise ValueError("rpt_diagram_order_invalid")
        if [point.x for point in self.diagram_points] != sorted(point.x for point in self.diagram_points):
            raise ValueError("rpt_diagram_time_invalid")
        if self.diagram_points[0].x != 0 or self.diagram_points[-1].x != 2000:
            raise ValueError("rpt_diagram_bounds_invalid")
        if [point.y for point in self.diagram_points] != [100, 0, 0, 100, 0, 0, 100]:
            raise ValueError("rpt_diagram_speed_invalid")
        if _fraction(self.phases[0].start_s) != 0 or _fraction(self.phases[-1].end_s) != _fraction(self.cycle_duration_s_exact):
            raise ValueError("rpt_phase_duration_invalid")
        if _fraction(self.phases[0].end_s) != _fraction(self.phases[1].start_s):
            raise ValueError("rpt_phase_continuity_invalid")
        if _fraction(self.phases[1].end_s) != _fraction(self.phases[2].start_s):
            raise ValueError("rpt_phase_continuity_invalid")
        return self


def _fraction(value: ExactRationalResultModel) -> Fraction:
    return Fraction(int(value.numerator), int(value.denominator))
