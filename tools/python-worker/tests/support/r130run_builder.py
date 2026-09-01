from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import hashlib
import json
from pathlib import Path
import stat
from zipfile import ZIP_STORED, ZipFile, ZipInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT_ROOT = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1"
M9A_BASE_PACKAGE = SNAPSHOT_ROOT / "m9a" / "packages" / "normal_final_rbd.r130run"
PACKAGE_ID = "019d3c80-3d21-7a65-8e5a-111111111111"
RUN_ID = "normal_final_rbd"
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
        "source_snapshot_sha256": "0" * 64,
        "producer": {
            "name": "R130SH",
            "version": "synthetic-m03a",
            "build_id": "downstream_synthetic_contract_fixture",
            "git_commit": "downstream-synthetic-m9a",
        },
        "files": files_value,
    }
    initial_snapshot = manifest["source_snapshot_sha256"]
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        manifest_files = []
    if manifest.get("source_snapshot_sha256") == initial_snapshot:
        manifest["source_snapshot_sha256"] = _source_snapshot_hash(manifest.get("run_id"), manifest_files)
    checksum_lines = [
        f"{item['sha256']}  {item['size']}  {item['path']}"
        for item in sorted(
            (value for value in manifest_files if isinstance(value, dict)),
            key=_manifest_path,
        )
        if isinstance(item.get("sha256"), str) and isinstance(item.get("size"), int) and isinstance(item.get("path"), str)
    ]
    entries = [
        ("manifest.json", _json_bytes(manifest)),
        *payloads.items(),
        ("checksums.sha256", ("\n".join(checksum_lines) + "\n").encode("utf-8")),
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
    with ZipFile(M9A_BASE_PACKAGE, mode="r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        return {str(item["path"]): archive.read(str(item["path"])) for item in manifest["files"]}


def _source_snapshot_hash(run_id: JsonValue, files: list[JsonValue]) -> str:
    descriptors: list[dict[str, JsonValue]] = []
    for raw in files:
        if not isinstance(raw, dict):
            continue
        descriptor: dict[str, JsonValue] = {
            "path": raw.get("path"),
            "media_type": raw.get("media_type"),
            "size": raw.get("size"),
            "sha256": raw.get("sha256"),
        }
        if "row_count" in raw:
            descriptor["row_count"] = raw["row_count"]
        descriptors.append(descriptor)
    descriptor_values: list[JsonValue] = [item for item in descriptors]
    value: dict[str, JsonValue] = {
        "schema_version": "r130sh.run-export-snapshot.v1",
        "run_id": run_id,
        "files": descriptor_values,
    }
    return hashlib.sha256(_json_bytes(value)).hexdigest()


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
