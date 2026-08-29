from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import hashlib
import json
from pathlib import Path
import stat
from zipfile import ZIP_STORED, ZipFile, ZipInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT_ROOT = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1"
PACKAGE_ID = "019d3c80-3d21-7a65-8e5a-111111111111"
RUN_ID = "019d3c80-3d21-7a65-8e5a-222222222222"
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
ManifestMutator = Callable[[dict[str, JsonValue]], None]


def build_synthetic_r130run(
    destination: Path,
    *,
    manifest_mutator: ManifestMutator | None = None,
    extra_entries: Iterable[tuple[str, bytes]] = (),
    payload_overrides: Mapping[str, bytes] | None = None,
    compression: int = ZIP_STORED,
) -> Path:
    payloads = _payloads()
    if payload_overrides is not None:
        payloads.update(payload_overrides)
    files: list[dict[str, JsonValue]] = []
    for path, content in payloads.items():
        item: dict[str, JsonValue] = {
            "path": path,
            "media_type": _media_type(path),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if path.endswith(".jsonl"):
            item["row_count"] = 0 if content == b"" else len(content.rstrip(b"\n").splitlines())
        if path == "measurements.csv":
            item["row_count"] = 1
        files.append(item)
    sorted_files: list[dict[str, JsonValue]] = sorted(files, key=_manifest_path)
    files_value: list[JsonValue] = [item for item in sorted_files]
    manifest: dict[str, JsonValue] = {
        "schema_version": "r130sh.run-package.v1",
        "package_id": PACKAGE_ID,
        "export_revision": 1,
        "run_id": RUN_ID,
        "package_kind": "final",
        "created_at_utc": "2026-08-29T12:00:00Z",
        "source_snapshot_sha256": hashlib.sha256(b"downstream_synthetic_contract_fixture").hexdigest(),
        "producer": {
            "name": "R130SH",
            "version": "synthetic-m03a",
            "build_id": "downstream_synthetic_contract_fixture",
            "git_commit": "f02f6d954246a5ab6f57d33dac724ce03d7fb841",
        },
        "files": files_value,
    }
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    checksum_lines = [f"{item['sha256']}  {item['path']}" for item in files]
    entries = [
        ("manifest.json", _json_bytes(manifest)),
        *payloads.items(),
        ("checksums.sha256", ("\n".join(sorted(checksum_lines)) + "\n").encode("utf-8")),
        *extra_entries,
    ]
    write_r130run(destination, entries, compression=compression)
    return destination


def write_r130run(destination: Path, entries: Iterable[tuple[str, bytes]], *, compression: int = ZIP_STORED) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, mode="w", compression=compression, allowZip64=True, strict_timestamps=True) as archive:
        for path, content in sorted(entries, key=lambda item: item[0]):
            info = ZipInfo("entry", date_time=(2026, 8, 29, 12, 0, 0))
            info.filename = path
            info.orig_filename = path
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, content)


def _payloads() -> dict[str, bytes]:
    plan = (SNAPSHOT_ROOT / "plan.rbd-rounding.example.json").read_bytes()
    provenance = (SNAPSHOT_ROOT / "provenance.example.json").read_bytes()
    event = json.loads((SNAPSHOT_ROOT / "event.example.json").read_text(encoding="utf-8"))
    accepted = (SNAPSHOT_ROOT / "accepted-projection.example.json").read_bytes()
    inspection = json.loads((SNAPSHOT_ROOT / "inspection.example.json").read_text(encoding="utf-8"))
    return {
        "plan/original.json": plan,
        "plan/effective.json": plan,
        "plan/amendments.jsonl": b"",
        "run-summary.json": _json_bytes({"schema_version": "downstream.synthetic.uncovered.v1", "run_id": RUN_ID}),
        "environment.json": _json_bytes({"schema_version": "downstream.synthetic.uncovered.v1", "run_id": RUN_ID}),
        "provenance.json": provenance,
        "events.jsonl": _json_bytes(event),
        "measurements.csv": (f"measurement_id,run_id\n019d3c80-3d21-7a65-8e5a-555555555555,{RUN_ID}\n").encode(),
        "measurement-descriptors.json": _json_bytes({"schema_version": "downstream.synthetic.uncovered.v1", "items": []}),
        "accepted-summary.json": accepted,
        "vibration-baseline.json": _json_bytes({"schema_version": "downstream.synthetic.uncovered.v1", "run_id": RUN_ID}),
        "inspections.json": _json_bytes([inspection]),
        "attachments/index.json": _json_bytes({"schema_version": "downstream.synthetic.uncovered.v1", "items": []}),
    }


def _json_bytes(value: JsonValue) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest_path(item: dict[str, JsonValue]) -> str:
    path = item["path"]
    if not isinstance(path, str):
        raise AssertionError("synthetic_manifest_path_not_string")
    return path


def _media_type(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".jsonl"):
        return "application/x-ndjson"
    if path.endswith(".csv"):
        return "text/csv"
    return "application/octet-stream"
