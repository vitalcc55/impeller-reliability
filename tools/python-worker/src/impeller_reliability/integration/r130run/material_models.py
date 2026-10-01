from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator
from pydantic.alias_generators import to_camel

from impeller_reliability.integration.r130run.models import RunPackageMaterialValidationReport

type PositiveIntegerText = Annotated[str, Field(pattern=r"^[1-9][0-9]*$", max_length=16 * 1024)]


class MaterialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


class MaterialOrigin(MaterialModel):
    project_id: str
    local_import_id: str
    package_id: str
    run_id: str
    export_revision: int = Field(ge=1, le=9_007_199_254_740_991)
    outer_package_sha256: str


class MaterialIdentity(MaterialModel):
    origin: MaterialOrigin
    kind: Literal["photo", "protocol"]
    material_id: str = Field(min_length=1, max_length=16 * 1024)


class MaterialActor(MaterialModel):
    employee_id: str
    full_name: str
    position: str


class PhotoActor(MaterialActor):
    model_config = ConfigDict(extra="ignore")
    legacy: bool


class ProtocolActor(MaterialModel):
    employee_id: str | None
    full_name: str | None
    position: str | None
    legacy: bool | None
    source_json: str


class InspectionFindings(MaterialModel):
    cracks: bool
    chips: bool
    deformation: bool
    partial_destruction: bool
    total_destruction: bool
    balancing_elements_state: Literal["intact", "damaged", "not_assessed"]
    other_findings: str


class InspectionMaterialData(MaterialModel):
    schema_version: Literal["r130sh.inspection.v1"]
    inspection_id: str
    run_id: str
    stage: Literal["pre_test", "post_trial_run", "vibration_pause", "post_rbd", "post_rpt", "post_pmn"]
    trip_index: PositiveIntegerText | None
    performed_at_utc: str
    run_elapsed_s: str
    actor: MaterialActor
    findings: InspectionFindings
    inspection_outcome: Literal["clear", "blocking_damage", "inconclusive"]
    comment: str
    attachment_ids: tuple[str, ...]


class PhotoMaterialData(MaterialModel):
    attachment_id: str
    run_id: str
    inspection_id: str | None
    media_type: Literal["image/jpeg", "image/png"]
    size: int
    sha256: str
    width_px: PositiveIntegerText
    height_px: PositiveIntegerText
    actor: PhotoActor
    attached_at_utc: str
    availability: Literal["available", "unavailable"]
    unavailable_reason: str | None


class ProtocolMaterialData(MaterialModel):
    schema_version: Literal["r130sh.protocol-release.v1"]
    run_id: str
    release_id: PositiveIntegerText
    revision_number: PositiveIntegerText
    protocol_number: str
    template_version: str
    content_sha256: str
    released_at_utc: str
    released_by_actor: ProtocolActor
    photo_ids: tuple[str, ...]
    pdf_size_bytes: int


class MaterialReference(MaterialModel):
    kind: Literal["inspection", "photo"]
    material_id: str
    status: Literal["resolved", "unresolved", "ambiguous"]


class MaterialItem[DataT: BaseModel](MaterialModel):
    source_index: int = Field(ge=0, le=9_007_199_254_740_991)
    material_id: str | None
    state: Literal["verified", "ambiguous", "unavailable", "too_large", "not_included"]
    data: SerializeAsAny[DataT] | None
    references: tuple[MaterialReference, ...] = ()
    detail: str | None = None

    @model_validator(mode="after")
    def validate_item_state(self) -> MaterialItem[DataT]:
        if (self.state in {"verified", "ambiguous", "unavailable"}) != (self.data is not None):
            raise ValueError("material_state_data_mismatch")
        if (self.state == "not_included") != (self.material_id is None):
            raise ValueError("material_state_identity_mismatch")
        return self


class MaterialPage[DataT: BaseModel](MaterialModel):
    origin: MaterialOrigin
    verification: RunPackageMaterialValidationReport
    items: tuple[MaterialItem[DataT], ...] = Field(max_length=50)
    next_cursor: str | None = Field(max_length=512)
    page_bound: Literal["item_limit", "byte_limit"] | None


class MaterialDetail[DataT: BaseModel](MaterialModel):
    origin: MaterialOrigin
    verification: RunPackageMaterialValidationReport
    item: MaterialItem[DataT]
