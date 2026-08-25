import os
from pathlib import Path
import subprocess
import sys


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
    assert '"pong":true' in stdout
    assert '"requestId":"shutdown"' in stdout
    assert stderr == ""
