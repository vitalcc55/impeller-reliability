from __future__ import annotations

from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from impeller_reliability.calculations.exact import exact_value
from impeller_reliability.calculations.exact_result_snapshot import ExactRationalResultModel
from impeller_reliability.calculations.pmn import PmnFailureReason


class PmnPhaseResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    phase: Literal["acceleration", "steady_rotation", "deceleration"]
    start_s: ExactRationalResultModel
    end_s: ExactRationalResultModel
    start_rpm: ExactRationalResultModel
    end_rpm: ExactRationalResultModel


class PmnDiagramPointResultModel(BaseModel):
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


class PmnFailureResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["calculated", "not_applicable"]
    cycles_to_failure: str | None = Field(pattern=r"^(?:0|[1-9][0-9]{0,63})$")
    reason_code: PmnFailureReason | None

    @model_validator(mode="after")
    def validate_outcome(self) -> PmnFailureResultModel:
        if self.status == "calculated":
            if self.cycles_to_failure is None or self.reason_code is not None:
                raise ValueError("pmn_failure_result_invalid")
        elif self.cycles_to_failure is not None or self.reason_code is None:
            raise ValueError("pmn_failure_result_invalid")
        return self


class PmnReferenceResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    algorithm_id: Literal["pmn_reference"]
    algorithm_version: Literal["1.0.0"]
    numeric_policy: Literal["exact_fraction_v1"]
    maximum_rpm: ExactRationalResultModel
    cycle_duration_s_exact: ExactRationalResultModel
    total_duration_s_exact: ExactRationalResultModel
    total_duration_min_exact: ExactRationalResultModel
    total_duration_h_exact: ExactRationalResultModel
    failure_result: PmnFailureResultModel
    phases: list[PmnPhaseResultModel] = Field(min_length=3, max_length=3)
    diagram_points: list[PmnDiagramPointResultModel] = Field(min_length=7, max_length=7)
    formula_references: list[str] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_ordered_profile(self) -> PmnReferenceResultModel:
        exact_values = [
            self.maximum_rpm,
            self.cycle_duration_s_exact,
            self.total_duration_s_exact,
            self.total_duration_min_exact,
            self.total_duration_h_exact,
            *(value for phase in self.phases for value in (phase.start_s, phase.end_s, phase.start_rpm, phase.end_rpm)),
        ]
        if any(not _is_canonical_exact(value) for value in exact_values):
            raise ValueError("pmn_exact_value_invalid")
        maximum_rpm = _fraction(self.maximum_rpm)
        cycle_duration = _fraction(self.cycle_duration_s_exact)
        total_duration = _fraction(self.total_duration_s_exact)
        if maximum_rpm <= 0 or cycle_duration <= 0 or total_duration <= 0:
            raise ValueError("pmn_result_range_invalid")
        cycles = total_duration / cycle_duration
        if cycles <= 0 or cycles.denominator != 1:
            raise ValueError("pmn_total_cycles_invalid")
        if total_duration / 60 != _fraction(self.total_duration_min_exact) or total_duration / 3600 != _fraction(self.total_duration_h_exact):
            raise ValueError("pmn_duration_conversion_invalid")
        if [phase.phase for phase in self.phases] != ["acceleration", "steady_rotation", "deceleration"]:
            raise ValueError("pmn_phase_order_invalid")
        if [point.boundary for point in self.diagram_points] != [
            "cycle_start",
            "acceleration_end",
            "steady_end",
            "cycle_end",
            "repeat_acceleration_end",
            "repeat_steady_end",
            "repeat_cycle_end",
        ]:
            raise ValueError("pmn_diagram_order_invalid")
        if [point.x for point in self.diagram_points] != sorted(point.x for point in self.diagram_points):
            raise ValueError("pmn_diagram_time_invalid")
        if self.diagram_points[0].x != 0 or self.diagram_points[-1].x != 2000:
            raise ValueError("pmn_diagram_bounds_invalid")
        if [point.y for point in self.diagram_points] != [100, 0, 0, 100, 0, 0, 100]:
            raise ValueError("pmn_diagram_speed_invalid")
        if _fraction(self.phases[0].start_s) != 0 or _fraction(self.phases[-1].end_s) != _fraction(self.cycle_duration_s_exact):
            raise ValueError("pmn_phase_duration_invalid")
        if _fraction(self.phases[0].end_s) != _fraction(self.phases[1].start_s):
            raise ValueError("pmn_phase_continuity_invalid")
        if _fraction(self.phases[1].end_s) != _fraction(self.phases[2].start_s):
            raise ValueError("pmn_phase_continuity_invalid")
        if _fraction(self.phases[0].start_rpm) != 0 or _fraction(self.phases[-1].end_rpm) != 0:
            raise ValueError("pmn_phase_speed_boundary_invalid")
        if [(_fraction(phase.start_rpm), _fraction(phase.end_rpm)) for phase in self.phases] != [
            (Fraction(0), maximum_rpm),
            (maximum_rpm, maximum_rpm),
            (maximum_rpm, Fraction(0)),
        ]:
            raise ValueError("pmn_phase_speed_invalid")
        if any(_fraction(phase.start_s) > _fraction(phase.end_s) for phase in self.phases):
            raise ValueError("pmn_phase_order_invalid")
        return self


def _fraction(value: ExactRationalResultModel) -> Fraction:
    return Fraction(int(value.numerator), int(value.denominator))


def _is_canonical_exact(value: ExactRationalResultModel) -> bool:
    expected = exact_value(_fraction(value))
    return value.numerator == expected.numerator and value.denominator == expected.denominator and value.decimal == expected.decimal and value.decimal_preview == expected.decimal_preview
