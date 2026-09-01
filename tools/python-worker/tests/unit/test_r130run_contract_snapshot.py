from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

SNAPSHOT_ROOT = Path(__file__).resolve().parents[4] / "fixtures" / "contracts" / "r130run" / "v1"
M9A_SNAPSHOT_ROOT = SNAPSHOT_ROOT / "m9a"
M9A_INDEX_GIT_BLOB = "7380e90121fc7ad1dcb12c22baaeab98affc3698"
M9A_INDEX_SHA256 = "e5927b6f8c9b34a8c99614873a56ce96246cc19f0dd7458b9964a6e970f086bb"
M9A_INDEX_SIZE = 5552
EXPECTED_SOURCE = {
    "README.md": (
        "docs/contracts/r130run/v1/README.md",
        "bf9f5396a1a99e3c9a389d1a6eac0d06d8de9d5e",
        "8f0f8c2b10a9b7e8c0b525d020b5f8f3566e7c0c793561fe8de91e29b5328319",
    ),
    "manifest.final.example.json": (
        "docs/contracts/r130run/v1/manifest.final.example.json",
        "a0e2285c3360756076e08d1eb8ea75578d3d3f38",
        "4655435dfcd08e831ed2f7757966e17ff50bc1fd1a8c36b4b0e0a47381b4e540",
    ),
    "plan.rbd-rounding.example.json": (
        "docs/contracts/r130run/v1/plan.rbd-rounding.example.json",
        "80bc4e3b277cfc7e55100cdd924051b7dde3ea17",
        "3c8436a92db33cf55f0ea82f79ff889eb99e912a6095d38089fa3cb20e54f650",
    ),
    "measurement.example.json": (
        "docs/contracts/r130run/v1/measurement.example.json",
        "fe834e4f65ebfb5bc657c73dee1145053a4ccf0a",
        "225d2231449e37b3a90981a6c518d369fd4910d6dbe58fcee5b6bf03d50c811d",
    ),
    "accepted-projection.example.json": (
        "docs/contracts/r130run/v1/accepted-projection.example.json",
        "4f3d4ffffaa1a4d62bbe6919d36e2ebfdb78e15b",
        "b121fd15865e0da95a40a79aea6640f49879104bc2845e47d6b76a72ff70a3a6",
    ),
    "event.example.json": (
        "docs/contracts/r130run/v1/event.example.json",
        "4c551ac9d3c5e3223636e857ad20188c6dc3ef90",
        "75a95417206a97f37e4ad5e4b754144b9b922cef72211ecc9ea0f2338043097e",
    ),
    "inspection.example.json": (
        "docs/contracts/r130run/v1/inspection.example.json",
        "89cb6022057f7c3ce837b1daa6b9d5aad75ec324",
        "06d3bc874d1729e545780a8fa8dff84aad98b5e079647e22ba70d631589098de",
    ),
    "provenance.example.json": (
        "docs/contracts/r130run/v1/provenance.example.json",
        "bc16f167c19b39d7703617c030a6d9c0f2ea67eb",
        "5307a70233e50c0863d055c35ec3d893bf40a7f8a9edce5c28fd572063ffc7e1",
    ),
    "m09a-expected-fixtures.json": (
        "docs/contracts/r130run/v1/m09a-expected-fixtures.json",
        "d302ee2a48a9a1469fb1cd7fe644bff138cd9ba4",
        "f1e1e6bdf6f4dbdf687831debeaeaa7f26bb95c4e0c6553fee3aa54e37fbf9a2",
    ),
}


def test_r130run_contract_snapshot_matches_pinned_upstream_hashes() -> None:
    source = json.loads((SNAPSHOT_ROOT / "UPSTREAM_SOURCE.json").read_text(encoding="utf-8"))

    assert source["schemaVersion"] == 1
    assert source["repository"] == "https://github.com/vitalcc55/R130SH"
    assert source["commit"] == "f02f6d954246a5ab6f57d33dac724ce03d7fb841"
    assert source["snapshotRole"].startswith("historical M03A synthetic")
    assert source["snapshotCreatedAtUtc"] == "2026-08-29T19:36:09.996Z"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", source["snapshotCreatedAtUtc"])
    assert source["statement"] == "synthetic target examples, not M9a golden packages"
    entries = source["files"]
    assert len(entries) == len(EXPECTED_SOURCE)
    assert len({entry["snapshotPath"] for entry in entries}) == len(entries)
    assert len({entry["originalPath"] for entry in entries}) == len(entries)
    assert {entry["snapshotPath"] for entry in entries} == set(EXPECTED_SOURCE)
    assert "as-is-v7-baseline.example.json" not in EXPECTED_SOURCE

    for entry in entries:
        snapshot_path = SNAPSHOT_ROOT / entry["snapshotPath"]
        expected_original, expected_blob, expected_sha256 = EXPECTED_SOURCE[entry["snapshotPath"]]
        content = snapshot_path.read_bytes()
        assert snapshot_path.is_file()
        assert entry["originalPath"] == expected_original
        assert entry["gitBlobSha"] == expected_blob
        assert entry["sha256"] == expected_sha256
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        assert hashlib.sha1(f"blob {len(content)}\0".encode() + content, usedforsecurity=False).hexdigest() == expected_blob
        if snapshot_path.suffix == ".json":
            json.loads(content.decode("utf-8"))


def test_r130run_snapshot_contains_no_untracked_contract_materials() -> None:
    actual_files = {path.name for path in SNAPSHOT_ROOT.iterdir() if path.is_file()}
    assert actual_files == set(EXPECTED_SOURCE) | {"UPSTREAM_SOURCE.json"}


def test_m9a_snapshot_matches_exact_upstream_index_and_outer_hashes() -> None:
    source = json.loads((M9A_SNAPSHOT_ROOT / "UPSTREAM_SOURCE.json").read_text(encoding="utf-8"))
    index = json.loads((M9A_SNAPSHOT_ROOT / "package-index.json").read_text(encoding="utf-8"))

    assert source["schemaVersion"] == "impeller-reliability.r130sh-m9a-snapshot-source.v1"
    assert source["repository"] == "vitalcc55/R130SH"
    assert source["branch"] == "codex/data-and-protocol-improvements"
    assert source["exactCommit"] == "01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63"
    assert source["upstreamPackageIndexPath"] == "tests/fixtures/r130run/v1/m9a/package-index.json"
    assert source["packageCount"] == 21
    assert source["scenarioCount"] == 18
    assert source["description"].startswith("Producer-generated M9a golden packages.")
    assert index["schema_version"] == "r130sh.m09a-golden-catalog.v1"
    assert len(index["packages"]) == 21
    assert len({entry["case_name"] for entry in index["packages"]}) == 18

    provenance_paths = [entry["path"] for entry in source["files"]]
    assert len(provenance_paths) == len(set(provenance_paths)) == 22
    provenance_by_path = {entry["path"]: entry for entry in source["files"]}
    indexed_by_path = {entry["path"]: entry for entry in index["packages"]}
    assert set(provenance_by_path) == {"package-index.json"} | set(indexed_by_path)

    index_bytes = (M9A_SNAPSHOT_ROOT / "package-index.json").read_bytes()
    assert len(index_bytes) == M9A_INDEX_SIZE
    assert hashlib.sha256(index_bytes).hexdigest() == M9A_INDEX_SHA256
    assert _git_blob_sha(index_bytes) == M9A_INDEX_GIT_BLOB
    assert provenance_by_path["package-index.json"]["gitBlobSha"] == M9A_INDEX_GIT_BLOB
    for relative_path, expected in indexed_by_path.items():
        package_path = M9A_SNAPSHOT_ROOT / relative_path
        payload = package_path.read_bytes()
        provenance = provenance_by_path[relative_path]
        assert package_path.is_file()
        assert len(payload) == expected["size"] == provenance["size"]
        assert hashlib.sha256(payload).hexdigest() == expected["sha256"] == provenance["outerSha256"]
        assert _git_blob_sha(payload) == provenance["gitBlobSha"]


def test_m9a_snapshot_contains_exactly_the_indexed_packages() -> None:
    index = json.loads((M9A_SNAPSHOT_ROOT / "package-index.json").read_text(encoding="utf-8"))
    expected_packages = {entry["path"] for entry in index["packages"]}
    actual_packages = {path.relative_to(M9A_SNAPSHOT_ROOT).as_posix() for path in (M9A_SNAPSHOT_ROOT / "packages").rglob("*") if path.is_file()}
    assert actual_packages == expected_packages
    assert {path.name for path in M9A_SNAPSHOT_ROOT.iterdir()} == {
        "README.md",
        "UPSTREAM_SOURCE.json",
        "package-index.json",
        "packages",
    }


def _git_blob_sha(payload: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(payload)}\0".encode() + payload,
        usedforsecurity=False,
    ).hexdigest()
