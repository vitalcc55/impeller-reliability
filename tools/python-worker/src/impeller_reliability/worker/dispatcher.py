from importlib.metadata import version
from pathlib import Path
import platform

from impeller_reliability import __version__
from impeller_reliability.persistence.sqlite_health import SCHEMA_VERSION, check_storage
from impeller_reliability.protocol.envelopes import (
    HandshakeRequest,
    HandshakeResult,
    Operation,
    PingRequest,
    PingResult,
    RequestEnvelope,
    ShutdownRequest,
    ShutdownResult,
    StorageHealthResult,
    SuccessResponse,
    SuccessResponseType,
)

CAPABILITIES: list[Operation] = ["system.handshake", "system.ping", "system.shutdown", "storage.health"]


class Dispatcher:
    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory
        self.shutdown_requested = False

    def dispatch(self, request: RequestEnvelope) -> SuccessResponseType:
        if isinstance(request, HandshakeRequest):
            return SuccessResponse[HandshakeResult](
                requestId=request.requestId,
                revision=request.revision,
                result=HandshakeResult(
                    workerVersion=__version__,
                    protocolVersions=[1],
                    pythonVersion=platform.python_version(),
                    numpyVersion=version("numpy"),
                    scipyVersion=version("scipy"),
                    databaseSchemaVersions=[SCHEMA_VERSION],
                    algorithmVersions={},
                    supportedRunPackageSchemas=[],
                    supportedPlanSchemas=[],
                    capabilities=CAPABILITIES,
                ),
            )
        if isinstance(request, PingRequest):
            return SuccessResponse[PingResult](
                requestId=request.requestId,
                revision=request.revision,
                result=PingResult(),
            )
        if isinstance(request, ShutdownRequest):
            self.shutdown_requested = True
            return SuccessResponse[ShutdownResult](
                requestId=request.requestId,
                revision=request.revision,
                result=ShutdownResult(),
            )
        return SuccessResponse[StorageHealthResult](
            requestId=request.requestId,
            revision=request.revision,
            result=StorageHealthResult.model_validate(check_storage(self._state_directory / "health.sqlite")),
        )
