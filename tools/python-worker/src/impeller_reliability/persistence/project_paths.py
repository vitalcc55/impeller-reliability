from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING

from impeller_reliability.persistence.project_errors import ProjectOperationError

if TYPE_CHECKING:
    from impeller_reliability.persistence.project_manifest import ProjectManifest

_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400


def inspect_reserved_file(path: Path, label: str, *, allow_missing: bool = False) -> bool:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return False
        raise ProjectOperationError("corrupt_project", f"Зарезервированный файл {label} отсутствует.") from None
    except OSError as error:
        raise ProjectOperationError("corrupt_project", f"Не удалось проверить зарезервированный файл {label}.") from error
    _reject_reparse_point(path, path_stat, label)
    if not stat.S_ISREG(path_stat.st_mode):
        raise ProjectOperationError("corrupt_project", f"Зарезервированный путь {label} не является обычным файлом.")
    if path_stat.st_nlink != 1:
        raise ProjectOperationError("corrupt_project", f"Зарезервированный файл {label} не должен быть hard link.")
    return True


def inspect_reserved_directory(path: Path, label: str) -> None:
    try:
        path_stat = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise ProjectOperationError("corrupt_project", f"Зарезервированный каталог {label} отсутствует.") from error
    except OSError as error:
        raise ProjectOperationError("corrupt_project", f"Не удалось проверить зарезервированный каталог {label}.") from error
    _reject_reparse_point(path, path_stat, label)
    if not stat.S_ISDIR(path_stat.st_mode):
        raise ProjectOperationError("corrupt_project", f"Зарезервированный путь {label} не является каталогом.")


def inspect_opened_regular_file(descriptor: int, label: str) -> None:
    try:
        path_stat = os.fstat(descriptor)
    except OSError as error:
        raise ProjectOperationError("corrupt_project", f"Не удалось проверить открытый файл {label}.") from error
    if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        raise ProjectOperationError("corrupt_project", f"Открытый файл {label} не является отдельным обычным файлом.")


def validate_project_container(project_path: Path, manifest: ProjectManifest) -> None:
    inspect_reserved_directory(project_path, ".irproj")
    inspect_reserved_file(project_path / "project-manifest.json", "project-manifest.json")
    inspect_reserved_file(project_path / manifest.databaseFile, manifest.databaseFile)
    inspect_reserved_file(project_path / ".project.lock", ".project.lock", allow_missing=True)
    inspect_reserved_directory(project_path / "backups", "backups/")
    inspect_reserved_file(
        project_path / f"{manifest.databaseFile}-wal",
        f"{manifest.databaseFile}-wal",
        allow_missing=True,
    )
    inspect_reserved_file(
        project_path / f"{manifest.databaseFile}-shm",
        f"{manifest.databaseFile}-shm",
        allow_missing=True,
    )
    inspect_reserved_file(
        project_path / f"{manifest.databaseFile}-journal",
        f"{manifest.databaseFile}-journal",
        allow_missing=True,
    )


def _reject_reparse_point(path: Path, path_stat: os.stat_result, label: str) -> None:
    file_attributes = path_stat.st_file_attributes
    if path.is_symlink() or path.is_junction() or file_attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE:
        raise ProjectOperationError("corrupt_project", f"Зарезервированный путь {label} не должен быть symlink или reparse point.")
