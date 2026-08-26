import json
import os
from pathlib import Path
import subprocess
import sys

from impeller_reliability.application.project_service import ProjectService


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
