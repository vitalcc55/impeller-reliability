from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityId = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DocumentEvidenceSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    documentId: EntityId
    recordRevision: int = Field(ge=1)
    documentKind: Literal[
        "technical_specification",
        "individual_test_method",
        "typical_test_method",
        "customer_requirement",
        "test_request",
        "operational_documentation",
        "standard",
        "drawing",
        "measurement_or_attestation_record",
        "other",
    ]
    title: str = Field(max_length=300)
    designation: str = Field(max_length=200)
    revisionLabel: str = Field(max_length=200)
    fileSha256: Sha256 | None
    locator: str = Field(max_length=1000)
