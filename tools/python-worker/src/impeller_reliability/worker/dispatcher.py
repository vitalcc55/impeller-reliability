from importlib.metadata import version
from pathlib import Path
import platform

from impeller_reliability import __version__
from impeller_reliability.persistence.sqlite_health import SCHEMA_VERSION, check_storage
from impeller_reliability.protocol.envelopes import RequestEnvelope, SuccessResponse


class Dispatcher:
    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory
        self.shutdown_requested = False

    def dispatch(self, request: RequestEnvelope) -> SuccessResponse:
        operation = request.operation
        if operation == "system.handshake":
            result: dict[str, object] = {
                "workerVersion": __version__,
                "protocolVersions": [1],
                "pythonVersion": platform.python_version(),
                "numpyVersion": version("numpy"),
                "scipyVersion": version("scipy"),
                "databaseSchemaVersions": [SCHEMA_VERSION],
                "algorithmVersions": {},
                "supportedRunPackageSchemas": [],
                "supportedPlanSchemas": [],
                "capabilities": [
                    "system.handshake",
                    "system.ping",
                    "system.shutdown",
                    "storage.health",
                ],
            }
        elif operation == "system.ping":
            result = {"pong": True}
        elif operation == "storage.health":
            result = check_storage(self._state_directory / "health.sqlite")
        else:
            self.shutdown_requested = True
            result = {"accepted": True}
        return SuccessResponse(requestId=request.requestId, result=result, evidence={}, warnings=[])
