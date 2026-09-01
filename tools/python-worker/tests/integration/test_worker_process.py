import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4

from pydantic import TypeAdapter

from impeller_reliability.application.project_service import ProjectService
from support.r130run_builder import build_synthetic_r130run

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
M9A_PACKAGE = REPOSITORY_ROOT / "fixtures" / "contracts" / "r130run" / "v1" / "m9a" / "packages" / "normal_final_rbd.r130run"
OBJECT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
OBJECT_LIST_ADAPTER: TypeAdapter[list[object]] = TypeAdapter(list[object])


def test_worker_jsonl_lifecycle(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["IMPELLER_STATE_DIR"] = str(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "impeller_reliability.worker.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    stdout, stderr = process.communicate(
        """{"protocolVersion":1,"requestId":"ping","kind":"request","operation":"system.ping","revision":0,"deadlineMs":5000,"payload":{}}
{"protocolVersion":1,"requestId":"shutdown","kind":"request","operation":"system.shutdown","revision":1,"deadlineMs":5000,"payload":{}}
""",
        timeout=10,
    )
    assert process.returncode == 0
    assert '"requestId":"ping"' in stdout
    assert '"revision":0' in stdout
    assert '"pong":true' in stdout
    assert '"requestId":"shutdown"' in stdout
    assert '"revision":1' in stdout
    assert stderr == ""


def test_worker_writes_project_cyrillic_as_utf8(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["IMPELLER_STATE_DIR"] = str(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "impeller_reliability.worker.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    create: dict[str, object] = {
        "protocolVersion": 1,
        "requestId": "create",
        "kind": "request",
        "operation": "project.create",
        "revision": 0,
        "deadlineMs": 5000,
        "payload": {
            "path": str(tmp_path / "Проект с пробелами.irproj"),
            "applicationInstanceId": "test-instance",
            "applicationVersion": "0.1.0",
            "draft": {"name": "Новый проект", "projectNumber": "ИР-1", "description": "Кириллица", "status": "draft"},
        },
    }
    shutdown: dict[str, object] = {
        "protocolVersion": 1,
        "requestId": "shutdown",
        "kind": "request",
        "operation": "system.shutdown",
        "revision": 1,
        "deadlineMs": 5000,
        "payload": {},
    }
    stdout, stderr = process.communicate(
        f"{json.dumps(create, ensure_ascii=False)}\n{json.dumps(shutdown)}\n",
        timeout=10,
    )
    assert process.returncode == 0
    assert '"name":"Новый проект"' in stdout
    assert '"projectNumber":"ИР-1"' in stdout
    assert stderr == ""
    reopened = ProjectService()
    overview = reopened.open(path=str(tmp_path / "Проект с пробелами.irproj"), application_instance_id="after-shutdown")
    assert overview.name == "Новый проект"
    reopened.close()


def test_worker_validation_job_emits_only_correlated_jsonl_responses(tmp_path: Path) -> None:
    package = build_synthetic_r130run(tmp_path / "candidate.r130run")
    job_id = str(uuid4())
    environment = os.environ.copy()
    environment["IMPELLER_STATE_DIR"] = str(tmp_path / "state")
    process = subprocess.Popen(
        [sys.executable, "-m", "impeller_reliability.worker.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    messages: list[dict[str, object]] = [
        {
            "protocolVersion": 1,
            "requestId": "start",
            "kind": "request",
            "operation": "runPackageValidation.start",
            "revision": 1,
            "deadlineMs": 5_000,
            "payload": {"jobId": job_id, "sourcePath": str(package), "validationBudgetMs": 30_000},
        },
        {
            "protocolVersion": 1,
            "requestId": "ping",
            "kind": "request",
            "operation": "system.ping",
            "revision": 2,
            "deadlineMs": 5_000,
            "payload": {},
        },
        {
            "protocolVersion": 1,
            "requestId": "get",
            "kind": "request",
            "operation": "runPackageValidation.get",
            "revision": 3,
            "deadlineMs": 5_000,
            "payload": {"jobId": job_id},
        },
        {
            "protocolVersion": 1,
            "requestId": "shutdown",
            "kind": "request",
            "operation": "system.shutdown",
            "revision": 4,
            "deadlineMs": 5_000,
            "payload": {},
        },
    ]

    stdout, stderr = process.communicate(
        "".join(f"{json.dumps(message, ensure_ascii=False)}\n" for message in messages),
        timeout=10,
    )

    responses = [json.loads(line) for line in stdout.splitlines()]
    assert process.returncode == 0
    assert [response["requestId"] for response in responses] == ["start", "ping", "get", "shutdown"]
    assert [response["revision"] for response in responses] == [1, 2, 3, 4]
    assert all(response["kind"] == "response" for response in responses)
    assert stderr == ""


def test_worker_process_imports_and_reads_a_producer_m9a_package(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["IMPELLER_STATE_DIR"] = str(tmp_path / "state")
    process = subprocess.Popen(
        [sys.executable, "-m", "impeller_reliability.worker.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    revision = 0

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal revision
        assert process.stdin is not None
        assert process.stdout is not None
        revision += 1
        message = {
            "protocolVersion": 1,
            "requestId": f"request-{revision}",
            "kind": "request",
            "operation": operation,
            "revision": revision,
            "deadlineMs": 30_000,
            "payload": payload,
        }
        process.stdin.write(f"{json.dumps(message, ensure_ascii=False)}\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if line == "":
            stderr = "" if process.stderr is None else process.stderr.read()
            raise AssertionError(f"worker_exited_without_response: {stderr}")
        response = OBJECT_ADAPTER.validate_json(line)
        assert response["kind"] == "response"
        return OBJECT_ADAPTER.validate_python(response["result"])

    project_path = tmp_path / "process-import.irproj"
    request(
        "project.create",
        {
            "path": str(project_path),
            "applicationInstanceId": "process-import",
            "applicationVersion": "0.1.0",
            "draft": {"name": "Process import", "projectNumber": "", "description": "", "status": "draft"},
        },
    )
    job_id = str(uuid4())
    snapshot = request(
        "runPackageImport.start",
        {"jobId": job_id, "sourcePath": str(M9A_PACKAGE), "allowDiagnosticPartial": False},
    )
    expires = monotonic() + 10
    while snapshot["state"] not in {"completed", "failed", "cancelled"} and monotonic() < expires:
        sleep(0.02)
        snapshot = request("runPackageImport.get", {"jobId": job_id})
    assert snapshot["state"] == "completed"
    listed = request("importedRun.list", {})
    listed_items = OBJECT_LIST_ADAPTER.validate_python(listed["items"])
    assert len(listed_items) == 1
    request("system.shutdown", {})
    assert process.wait(timeout=10) == 0
    assert process.stderr is not None
    assert process.stderr.read() == ""
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.close()
    process.stdout.close()
    process.stderr.close()
