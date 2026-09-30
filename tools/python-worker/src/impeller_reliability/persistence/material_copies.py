from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import RFC_4122, UUID

from impeller_reliability.persistence.project_errors import ProjectOperationError
from impeller_reliability.persistence.project_paths import (
    ManagedFileIdentity,
    discard_opened_managed_file,
    inspect_opened_regular_file,
    inspect_reserved_file,
    open_managed_file_for_discard,
    opened_file_identity,
    pin_reserved_directory,
)
from impeller_reliability.worker.deadline import RequestDeadline


def discard_material_copy(
    directory: Path,
    copy_id: str,
    media_type: Literal["image/jpeg", "image/png", "application/pdf"],
    expected: ManagedFileIdentity,
    deadline: RequestDeadline,
) -> bool:
    """Main chooses lifecycle; worker disposes only the identified copied object."""
    try:
        identifier = UUID(copy_id)
    except ValueError as error:
        raise ProjectOperationError("validation_error", "Идентификатор копии недопустим.") from error
    if str(identifier) != copy_id or identifier.version != 4 or identifier.variant != RFC_4122:
        raise ProjectOperationError("validation_error", "Идентификатор копии должен быть canonical UUID v4.")
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}[media_type]
    path = directory / f"{copy_id}{suffix}"
    try:
        if not directory.is_absolute() or directory.resolve(strict=True) != directory or any(parent.suffix.lower() == ".irproj" for parent in (directory, *directory.parents)):
            raise ProjectOperationError("validation_error", "Удаление копии требует разрешённого каталога вне дела.")
        deadline.check("material_copy_discard")
        with pin_reserved_directory(directory, "material copy directory"):
            try:
                with open_managed_file_for_discard(path) as source:
                    inspect_opened_regular_file(source.fileno(), "material copy")
                    inspect_reserved_file(path, "material copy")
                    if opened_file_identity(source.fileno()) != expected:
                        raise ProjectOperationError("file_integrity_mismatch", "Подменённая копия не удаляется как принадлежащая операции.")
                    deadline.check("material_copy_discard_commit")
                    discard_opened_managed_file(source.fileno())
                return True
            except FileNotFoundError:
                return False
    except ProjectOperationError as error:
        if error.code == "corrupt_project":
            raise ProjectOperationError("file_integrity_mismatch", "Копия не является безопасным обычным файлом.") from error
        raise
    except OSError as error:
        raise ProjectOperationError("storage_error", "Не удалось удалить проверенную временную копию.") from error
