from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from impeller_reliability.integration.r130run.material_models import (
    InspectionMaterialData,
    MaterialDetail,
    MaterialItem,
    MaterialOrigin,
    MaterialPage,
    MaterialReference,
    PhotoMaterialData,
    ProtocolActor,
    ProtocolMaterialData,
)
from impeller_reliability.integration.r130run.models import RunPackageMaterialValidationReport
from impeller_reliability.integration.r130run.validator import JsonValue, RunPackageManifest
from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.worker.deadline import RequestDeadline

MAX_MATERIAL_TEXT_BYTES = 16 * 1024
MAX_MATERIAL_RECORD_BYTES = 64 * 1024
MAX_MATERIAL_PAGE_BYTES = 256 * 1024
OBJECT = TypeAdapter(dict[str, JsonValue])
RECORDS = TypeAdapter(list[dict[str, JsonValue]])
STRINGS = TypeAdapter(list[str])
TEXT = TypeAdapter(str)
INTEGER = TypeAdapter(int)
CURSOR = TypeAdapter(tuple[Literal["source_materials.v1"], Literal["inspections", "photos"], str, str, str, int, str, int])


@dataclass(frozen=True, slots=True)
class MaterialSnapshot:
    origin: MaterialOrigin
    manifest: RunPackageManifest
    verification: RunPackageMaterialValidationReport
    payloads: dict[str, JsonValue]


def _records(snapshot: MaterialSnapshot, path: str, field: str) -> list[dict[str, JsonValue]]:
    envelope = OBJECT.validate_python(snapshot.payloads[path], strict=True)
    return RECORDS.validate_python(envelope[field], strict=True)


def _counts(records: list[dict[str, JsonValue]], field: str, deadline: RequestDeadline) -> dict[str, int]:
    result: dict[str, int] = {}
    for record in records:
        deadline.check("material_reference_index")
        identity = TEXT.validate_python(record[field], strict=True)
        result[identity] = result.get(identity, 0) + 1
    return result


def _reference(kind: Literal["inspection", "photo"], identity: str, counts: dict[str, int]) -> MaterialReference:
    count = counts.get(identity, 0)
    return MaterialReference(kind=kind, material_id=identity, status="resolved" if count == 1 else "unresolved" if count == 0 else "ambiguous")


def _json_bytes(value: JsonValue) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _text_exceeds_bound(root: JsonValue, deadline: RequestDeadline) -> bool:
    stack = [root]
    while stack:
        deadline.check("material_response_bounds")
        value = stack.pop()
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_MATERIAL_TEXT_BYTES:
            return True
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return False


def _oversized[DataT: BaseModel](index: int, identity: str, data_type: type[DataT]) -> MaterialItem[DataT]:
    return MaterialItem[DataT](source_index=index, material_id=identity, state="too_large", data=None, detail="Метаданные превышают технический предел записи или текста; сведения не обрезаны.")


def _bounded_item[DataT: BaseModel](item: MaterialItem[DataT], raw: dict[str, JsonValue], data_type: type[DataT], deadline: RequestDeadline) -> MaterialItem[DataT]:
    serialized = item.model_dump_json()
    if (
        len(_json_bytes(raw)) > MAX_MATERIAL_RECORD_BYTES
        or _text_exceeds_bound(raw, deadline)
        or len(serialized.encode("utf-8")) > MAX_MATERIAL_RECORD_BYTES
        or _text_exceeds_bound(OBJECT.validate_json(serialized, strict=True), deadline)
    ):
        return _oversized(item.source_index, TEXT.validate_python(item.material_id, strict=True), data_type)
    return item


def _inspection_item(index: int, record: dict[str, JsonValue], own_ids: dict[str, int], photo_ids: dict[str, int], deadline: RequestDeadline) -> MaterialItem[InspectionMaterialData]:
    identity = TEXT.validate_python(record["inspection_id"], strict=True)
    if len(_json_bytes(record)) > MAX_MATERIAL_RECORD_BYTES or _text_exceeds_bound(record, deadline):
        return _oversized(index, identity, InspectionMaterialData)
    values: dict[str, object] = dict(record)
    trip = record["trip_index"]
    values["trip_index"] = None if trip is None else str(INTEGER.validate_python(trip, strict=True))
    references = tuple(STRINGS.validate_python(record["attachment_ids"], strict=True))
    values["attachment_ids"] = references
    data = InspectionMaterialData.model_validate(values)
    item = MaterialItem[InspectionMaterialData](
        source_index=index,
        material_id=identity,
        state="ambiguous" if own_ids[identity] != 1 else "verified",
        data=data,
        references=tuple(_reference("photo", value, photo_ids) for value in references),
    )
    return _bounded_item(item, record, InspectionMaterialData, deadline)


def _photo_item(index: int, record: dict[str, JsonValue], own_ids: dict[str, int], inspection_ids: dict[str, int], deadline: RequestDeadline) -> MaterialItem[PhotoMaterialData]:
    identity = TEXT.validate_python(record["attachment_id"], strict=True)
    if len(_json_bytes(record)) > MAX_MATERIAL_RECORD_BYTES or _text_exceeds_bound(record, deadline):
        return _oversized(index, identity, PhotoMaterialData)
    values: dict[str, object] = {field: record.get(field) for field in PhotoMaterialData.model_fields}
    values["width_px"] = str(INTEGER.validate_python(record["width_px"], strict=True))
    values["height_px"] = str(INTEGER.validate_python(record["height_px"], strict=True))
    values["unavailable_reason"] = record.get("unavailable_reason") if record["availability"] == "unavailable" else None
    data = PhotoMaterialData.model_validate(values)
    references = () if data.inspection_id is None else (_reference("inspection", data.inspection_id, inspection_ids),)
    item = MaterialItem[PhotoMaterialData](
        source_index=index, material_id=identity, state="ambiguous" if own_ids[identity] != 1 else "unavailable" if data.availability == "unavailable" else "verified", data=data, references=references
    )
    return _bounded_item(item, record, PhotoMaterialData, deadline)


def _cursor_context(origin: MaterialOrigin, kind: Literal["inspections", "photos"]) -> tuple[str, str, str, str, str, int, str]:
    return ("source_materials.v1", kind, origin.project_id, origin.local_import_id, origin.package_id, origin.export_revision, origin.outer_package_sha256)


def _start_index(cursor: str | None, origin: MaterialOrigin, kind: Literal["inspections", "photos"], count: int) -> int:
    if cursor is None:
        return 0
    try:
        if not 1 <= len(cursor) <= 512:
            raise ValueError("cursor_length")
        raw = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
        fields = CURSOR.validate_json(raw, strict=True)
        if fields[:7] != _cursor_context(origin, kind) or not 0 <= fields[7] < count - 1:
            raise ValueError("cursor_identity")
        return fields[7] + 1
    except UnicodeError, ValueError, ValidationError, binascii.Error:
        raise ProjectOperationError("validation_error", "Cursor материалов не относится к выбранному списку и редакции.") from None


def _page[DataT: BaseModel](
    snapshot: MaterialSnapshot,
    kind: Literal["inspections", "photos"],
    records: list[dict[str, JsonValue]],
    cursor: str | None,
    limit: int,
    build: Callable[[int, dict[str, JsonValue]], MaterialItem[DataT]],
    deadline: RequestDeadline,
) -> MaterialPage[DataT]:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ProjectOperationError("validation_error", "Размер страницы материалов должен быть от 1 до 50.")
    start = _start_index(cursor, snapshot.origin, kind, len(records))
    items: list[MaterialItem[DataT]] = []
    next_index = start
    bound: Literal["item_limit", "byte_limit"] | None = None
    for index in range(start, min(start + limit, len(records))):
        deadline.check("material_page")
        item = build(index, records[index])
        candidate = MaterialPage[DataT](origin=snapshot.origin, verification=snapshot.verification, items=tuple([*items, item]), next_cursor=None, page_bound=None)
        if len(candidate.model_dump_json().encode("utf-8")) > MAX_MATERIAL_PAGE_BYTES - 1024:
            if not items:
                raise ProjectOperationError("file_too_large", "Ответ с проверкой материалов превышает технический предел страницы.")
            bound = "byte_limit"
            break
        items.append(item)
        next_index = index + 1
    next_cursor = None
    if next_index < len(records):
        encoded = json.dumps([*_cursor_context(snapshot.origin, kind), next_index - 1], separators=(",", ":")).encode("ascii")
        next_cursor = base64.urlsafe_b64encode(encoded).decode("ascii")
        bound = bound or "item_limit"
    result = MaterialPage[DataT](origin=snapshot.origin, verification=snapshot.verification, items=tuple(items), next_cursor=next_cursor, page_bound=bound)
    if len(result.model_dump_json().encode("utf-8")) > MAX_MATERIAL_PAGE_BYTES:
        raise ProjectOperationError("file_too_large", "Ответ материалов превышает технический предел страницы.")
    deadline.check("material_page_complete")
    return result


def inspection_page(snapshot: MaterialSnapshot, cursor: str | None, limit: int, deadline: RequestDeadline) -> MaterialPage[InspectionMaterialData]:
    records = _records(snapshot, "inspections.json", "inspections")
    own = _counts(records, "inspection_id", deadline)
    photos = _counts(_records(snapshot, "attachments/index.json", "attachments"), "attachment_id", deadline)
    return _page(snapshot, "inspections", records, cursor, limit, lambda index, record: _inspection_item(index, record, own, photos, deadline), deadline)


def photo_page(snapshot: MaterialSnapshot, cursor: str | None, limit: int, deadline: RequestDeadline) -> MaterialPage[PhotoMaterialData]:
    records = _records(snapshot, "attachments/index.json", "attachments")
    own = _counts(records, "attachment_id", deadline)
    inspections = _counts(_records(snapshot, "inspections.json", "inspections"), "inspection_id", deadline)
    return _page(snapshot, "photos", records, cursor, limit, lambda index, record: _photo_item(index, record, own, inspections, deadline), deadline)


def inspection_detail(snapshot: MaterialSnapshot, identity: str, deadline: RequestDeadline) -> MaterialDetail[InspectionMaterialData]:
    if not identity or len(identity.encode("utf-8")) > 200:
        raise ProjectOperationError("validation_error", "Идентификатор осмотра имеет недопустимый размер.")
    records = _records(snapshot, "inspections.json", "inspections")
    indices = [index for index, record in enumerate(records) if record["inspection_id"] == identity]
    if not indices:
        raise ProjectOperationError("entity_not_found", "Осмотр отсутствует в выбранной редакции.")
    if len(indices) != 1:
        raise ProjectOperationError("validation_error", "Идентификатор осмотра неоднозначен; подстановка запрещена.", details={"reason": "material_id_ambiguous"})
    own = _counts(records, "inspection_id", deadline)
    photos = _counts(_records(snapshot, "attachments/index.json", "attachments"), "attachment_id", deadline)
    item = _inspection_item(indices[0], records[indices[0]], own, photos, deadline)
    return MaterialDetail[InspectionMaterialData](origin=snapshot.origin, verification=snapshot.verification, item=item)


def photo_detail(snapshot: MaterialSnapshot, identity: str, deadline: RequestDeadline) -> MaterialDetail[PhotoMaterialData]:
    records = _records(snapshot, "attachments/index.json", "attachments")
    indices: list[int] = []
    for index, record in enumerate(records):
        deadline.check("material_photo_lookup")
        if record["attachment_id"] == identity:
            indices.append(index)
    if not indices:
        raise ProjectOperationError("entity_not_found", "Фотография отсутствует в выбранной редакции.")
    if len(indices) != 1:
        raise ProjectOperationError("validation_error", "Идентификатор фотографии неоднозначен; подстановка запрещена.", details={"reason": "material_id_ambiguous"})
    own = _counts(records, "attachment_id", deadline)
    inspections = _counts(_records(snapshot, "inspections.json", "inspections"), "inspection_id", deadline)
    item = _photo_item(indices[0], records[indices[0]], own, inspections, deadline)
    return MaterialDetail[PhotoMaterialData](origin=snapshot.origin, verification=snapshot.verification, item=item)


def protocol_detail(snapshot: MaterialSnapshot, deadline: RequestDeadline) -> MaterialDetail[ProtocolMaterialData]:
    raw = snapshot.payloads.get("protocol/release.json")
    if raw is None:
        return MaterialDetail[ProtocolMaterialData](
            origin=snapshot.origin, verification=snapshot.verification, item=MaterialItem[ProtocolMaterialData](source_index=0, material_id=None, state="not_included", data=None)
        )
    record = OBJECT.validate_python(raw, strict=True)
    identity = str(INTEGER.validate_python(record["release_id"], strict=True))
    if len(_json_bytes(record)) > MAX_MATERIAL_RECORD_BYTES or _text_exceeds_bound(record, deadline):
        item = _oversized(0, identity, ProtocolMaterialData)
    else:
        actor = OBJECT.validate_python(record["released_by_actor"], strict=True)
        employee_id, full_name, position, legacy = (actor.get(field) for field in ("employee_id", "full_name", "position", "legacy"))
        view = ProtocolActor(
            employee_id=employee_id if isinstance(employee_id, str) else None,
            full_name=full_name if isinstance(full_name, str) else None,
            position=position if isinstance(position, str) else None,
            legacy=legacy if isinstance(legacy, bool) else None,
            source_json=_json_bytes(actor).decode("utf-8"),
        )
        values: dict[str, object] = {field: record.get(field) for field in ProtocolMaterialData.model_fields}
        values.update(
            release_id=identity,
            revision_number=str(INTEGER.validate_python(record["revision_number"], strict=True)),
            released_by_actor=view,
            photo_ids=tuple(STRINGS.validate_python(record["photo_ids"], strict=True)),
            pdf_size_bytes=next(member.size for member in snapshot.manifest.files if member.path == "protocol/protocol.pdf"),
        )
        data = ProtocolMaterialData.model_validate(values)
        photos = _counts(_records(snapshot, "attachments/index.json", "attachments"), "attachment_id", deadline)
        item = _bounded_item(
            MaterialItem[ProtocolMaterialData](source_index=0, material_id=identity, state="verified", data=data, references=tuple(_reference("photo", value, photos) for value in data.photo_ids)),
            record,
            ProtocolMaterialData,
            deadline,
        )
    result = MaterialDetail[ProtocolMaterialData](origin=snapshot.origin, verification=snapshot.verification, item=item)
    deadline.check("material_protocol_complete")
    return result
