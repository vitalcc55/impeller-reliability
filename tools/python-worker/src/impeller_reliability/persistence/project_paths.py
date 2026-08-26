from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING

from impeller_reliability.persistence.project_errors import ProjectOperationError

if TYPE_CHECKING:
    from impeller_reliability.persistence.project_manifest import ProjectManifest

_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400


@dataclass(frozen=True, slots=True)
class PathIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class ProjectPathSnapshot:
    container: PathIdentity
    manifest: PathIdentity
    database: PathIdentity
    lock: PathIdentity | None
    backups: PathIdentity
    wal: PathIdentity | None
    shared_memory: PathIdentity | None
    rollback_journal: PathIdentity | None


def inspect_reserved_file(path: Path, label: str, *, allow_missing: bool = False) -> PathIdentity | None:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ProjectOperationError("corrupt_project", f"Зарезервированный файл {label} отсутствует.") from None
    except OSError as error:
        raise ProjectOperationError("corrupt_project", f"Не удалось проверить зарезервированный файл {label}.") from error
    _reject_reparse_point(path, path_stat, label)
    if not stat.S_ISREG(path_stat.st_mode):
        raise ProjectOperationError("corrupt_project", f"Зарезервированный путь {label} не является обычным файлом.")
    if path_stat.st_nlink != 1:
        raise ProjectOperationError("corrupt_project", f"Зарезервированный файл {label} не должен быть hard link.")
    return _identity(path_stat)


def inspect_reserved_directory(path: Path, label: str) -> PathIdentity:
    try:
        path_stat = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise ProjectOperationError("corrupt_project", f"Зарезервированный каталог {label} отсутствует.") from error
    except OSError as error:
        raise ProjectOperationError("corrupt_project", f"Не удалось проверить зарезервированный каталог {label}.") from error
    _reject_reparse_point(path, path_stat, label)
    if not stat.S_ISDIR(path_stat.st_mode):
        raise ProjectOperationError("corrupt_project", f"Зарезервированный путь {label} не является каталогом.")
    return _identity(path_stat)


def inspect_opened_regular_file(descriptor: int, label: str) -> PathIdentity:
    try:
        path_stat = os.fstat(descriptor)
    except OSError as error:
        raise ProjectOperationError("corrupt_project", f"Не удалось проверить открытый файл {label}.") from error
    if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        raise ProjectOperationError("corrupt_project", f"Открытый файл {label} не является отдельным обычным файлом.")
    return _identity(path_stat)


def inspect_project_container(project_path: Path, manifest: ProjectManifest) -> ProjectPathSnapshot:
    container = inspect_reserved_directory(project_path, ".irproj")
    manifest_identity = inspect_reserved_file(project_path / "project-manifest.json", "project-manifest.json")
    database_identity = inspect_reserved_file(project_path / manifest.databaseFile, manifest.databaseFile)
    lock_identity = inspect_reserved_file(project_path / ".project.lock", ".project.lock", allow_missing=True)
    backups_identity = inspect_reserved_directory(project_path / "backups", "backups/")
    wal_identity = inspect_reserved_file(
        project_path / f"{manifest.databaseFile}-wal",
        f"{manifest.databaseFile}-wal",
        allow_missing=True,
    )
    shared_memory_identity = inspect_reserved_file(
        project_path / f"{manifest.databaseFile}-shm",
        f"{manifest.databaseFile}-shm",
        allow_missing=True,
    )
    rollback_journal_identity = inspect_reserved_file(
        project_path / f"{manifest.databaseFile}-journal",
        f"{manifest.databaseFile}-journal",
        allow_missing=True,
    )
    if manifest_identity is None or database_identity is None:
        raise AssertionError("required_project_path_identity_missing")
    return ProjectPathSnapshot(
        container=container,
        manifest=manifest_identity,
        database=database_identity,
        lock=lock_identity,
        backups=backups_identity,
        wal=wal_identity,
        shared_memory=shared_memory_identity,
        rollback_journal=rollback_journal_identity,
    )


def verify_project_snapshot_stable(before_lock: ProjectPathSnapshot, after_lock: ProjectPathSnapshot) -> None:
    stable_before = (
        before_lock.container,
        before_lock.manifest,
        before_lock.database,
        before_lock.backups,
        before_lock.wal,
        before_lock.shared_memory,
        before_lock.rollback_journal,
    )
    stable_after = (
        after_lock.container,
        after_lock.manifest,
        after_lock.database,
        after_lock.backups,
        after_lock.wal,
        after_lock.shared_memory,
        after_lock.rollback_journal,
    )
    if stable_before != stable_after:
        raise ProjectOperationError("corrupt_project", "Зарезервированные пути проекта изменились во время открытия.")
    if before_lock.lock is not None and before_lock.lock != after_lock.lock:
        raise ProjectOperationError("corrupt_project", ".project.lock был подменён во время открытия.")
    if after_lock.lock is None:
        raise ProjectOperationError("corrupt_project", ".project.lock отсутствует после захвата блокировки.")


def _reject_reparse_point(path: Path, path_stat: os.stat_result, label: str) -> None:
    file_attributes = path_stat.st_file_attributes
    if path.is_symlink() or path.is_junction() or file_attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE:
        raise ProjectOperationError("corrupt_project", f"Зарезервированный путь {label} не должен быть symlink или reparse point.")


def _identity(path_stat: os.stat_result) -> PathIdentity:
    return PathIdentity(device=path_stat.st_dev, inode=path_stat.st_ino)
