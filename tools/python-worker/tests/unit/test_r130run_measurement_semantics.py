from __future__ import annotations

import csv
import io
from pathlib import Path
from zipfile import ZipFile

import pytest

from impeller_reliability.integration.r130run.m9a import (
    M9aContractError,
    MeasurementStreamSummary,
    MeasurementStreamValidator,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
M9A_PACKAGE = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1" / "m9a" / "packages" / "normal_final_rbd.r130run"


@pytest.mark.parametrize(
    ("attempt_disposition", "segment_disposition", "accepted", "accepted_elapsed_s", "is_valid"),
    [
        ("active", "included", "true", "0", True),
        ("accepted", "included", "true", "0", True),
        ("active", "excluded", "false", "", True),
        ("accepted", "excluded", "false", "", True),
        ("rejected", "included", "false", "", True),
        ("rejected", "excluded", "false", "", True),
        ("active", "included", "false", "", False),
        ("accepted", "included", "false", "", False),
        ("rejected", "included", "true", "0", False),
        ("rejected", "excluded", "true", "0", False),
        ("unknown", "included", "false", "", False),
        ("active", "unknown", "false", "", False),
        ("active", "excluded", "false", "1", False),
        ("active", "included", "true", "", False),
    ],
)
def test_measurement_acceptance_truth_table_is_exact(
    attempt_disposition: str,
    segment_disposition: str,
    accepted: str,
    accepted_elapsed_s: str,
    is_valid: bool,
) -> None:
    row = _row(
        sequence=1,
        attempt_disposition=attempt_disposition,
        segment_disposition=segment_disposition,
        accepted=accepted,
        accepted_elapsed_s=accepted_elapsed_s,
    )
    stream = MeasurementStreamValidator(row["run_id"])

    if is_valid:
        stream.consume(row)
        return
    with pytest.raises(M9aContractError):
        stream.consume(row)


def test_measurement_stream_recomputes_only_saved_accepted_sample_spans() -> None:
    rows = (
        _row(sequence=1, segment_id="segment-a", segment_elapsed_s="1.5", accepted_elapsed_s="0"),
        _row(sequence=2, segment_id="segment-a", segment_elapsed_s="3.5", accepted_elapsed_s="2"),
        _row(
            sequence=3,
            segment_id="segment-b",
            segment_elapsed_s="100",
            attempt_disposition="active",
            segment_disposition="excluded",
            accepted="false",
            accepted_elapsed_s="",
        ),
        _row(sequence=4, segment_id="segment-c", segment_elapsed_s="7", accepted_elapsed_s="2"),
        _row(
            sequence=5,
            segment_id="segment-d",
            segment_elapsed_s="200",
            attempt_disposition="rejected",
            segment_disposition="included",
            accepted="false",
            accepted_elapsed_s="",
        ),
        _row(sequence=6, segment_id="segment-c", segment_elapsed_s="12", accepted_elapsed_s="7"),
    )
    stream = MeasurementStreamValidator(rows[0]["run_id"])

    for row in rows:
        stream.consume(row)

    assert stream.summary().measurement_count == 6
    assert stream.summary().accepted_measurement_count == 4
    assert stream.summary().accepted_elapsed_s == "7"


def test_measurement_stream_rejects_an_incorrect_intermediate_accumulated_value() -> None:
    stream = MeasurementStreamValidator(_base_row()["run_id"])
    stream.consume(_row(sequence=1, segment_id="segment-a", segment_elapsed_s="2", accepted_elapsed_s="0"))

    with pytest.raises(M9aContractError, match="accepted_elapsed_mismatch"):
        stream.consume(_row(sequence=2, segment_id="segment-a", segment_elapsed_s="4", accepted_elapsed_s="3"))


def test_measurement_stream_accepts_upstream_scientific_package_number_text() -> None:
    stream = MeasurementStreamValidator(_base_row()["run_id"])
    stream.consume(_row(sequence=1, segment_elapsed_s="0", accepted_elapsed_s="0"))
    stream.consume(_row(sequence=2, segment_elapsed_s="1e-05", accepted_elapsed_s="1e-05"))

    assert stream.summary().accepted_elapsed_s == "1e-05"


def test_measurement_stream_maps_canonical_overflow_to_contract_error() -> None:
    stream = MeasurementStreamValidator(_base_row()["run_id"])
    with pytest.raises(M9aContractError, match="accepted_elapsed_invalid"):
        stream.consume(_row(sequence=1, segment_elapsed_s="1e1024", accepted_elapsed_s="0"))

    with pytest.raises(M9aContractError, match="accepted_summary_mismatch"):
        MeasurementStreamSummary(0, 0, "0").verify(0, "1e1024")


def test_measurement_stream_bounds_unique_segment_state_and_commits_only_valid_rows() -> None:
    stream = MeasurementStreamValidator(_base_row()["run_id"], max_segments=1)
    stream.consume(_row(sequence=1, segment_id="segment-a", segment_elapsed_s="2", accepted_elapsed_s="0"))
    with pytest.raises(M9aContractError, match="accepted_elapsed_mismatch"):
        stream.consume(_row(sequence=2, segment_id="segment-a", segment_elapsed_s="4", accepted_elapsed_s="3"))
    stream.consume(_row(sequence=2, segment_id="segment-a", segment_elapsed_s="3", accepted_elapsed_s="1"))
    with pytest.raises(M9aContractError, match="measurement_segment_limit"):
        stream.consume(_row(sequence=3, segment_id="segment-b", segment_elapsed_s="0", accepted_elapsed_s="1"))
    assert stream.summary().measurement_count == 2
    assert stream.summary().accepted_measurement_count == 2
    assert stream.summary().accepted_elapsed_s == "1"


@pytest.mark.parametrize("value", ["0.00", "NaN", "Infinity", "1e1024", "1e999999", "-1"])
def test_measurement_stream_rejects_noncanonical_or_invalid_decimal(value: str) -> None:
    stream = MeasurementStreamValidator(_base_row()["run_id"])
    with pytest.raises(M9aContractError, match="accepted_elapsed_invalid"):
        stream.consume(_row(sequence=1, accepted_elapsed_s=value))


def _row(
    *,
    sequence: int,
    attempt_disposition: str = "accepted",
    segment_disposition: str = "included",
    accepted: str = "true",
    accepted_elapsed_s: str = "0",
    segment_id: str = "segment-a",
    segment_elapsed_s: str = "0",
) -> dict[str, str]:
    row = _base_row()
    row.update(
        measurement_id=f"measurement-{sequence}",
        measurement_sequence=str(sequence),
        attempt_disposition=attempt_disposition,
        segment_disposition=segment_disposition,
        accepted=accepted,
        accepted_elapsed_s=accepted_elapsed_s,
        segment_id=segment_id,
        segment_elapsed_s=segment_elapsed_s,
    )
    return row


def _base_row() -> dict[str, str]:
    with ZipFile(M9A_PACKAGE) as archive, archive.open("measurements.csv") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        return dict(next(csv.DictReader(text)))
