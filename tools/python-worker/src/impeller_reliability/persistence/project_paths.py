from __future__ import annotations

import _winapi
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import io
import msvcrt
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING, BinaryIO

from impeller_reliability.persistence.project_errors import ProjectOperationError

if TYPE_CHECKING:
    from impeller_reliability.persistence.project_manifest import ProjectManifest

_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400


@dataclass(frozen=True)
class ManagedFileIdentity:
    volume_id: str
    file_id: str


def opened_file_identity(descriptor: int) -> ManagedFileIdentity:
    value = os.fstat(descriptor)
    if not 0 <= value.st_dev < 2**64 or not 0 < value.st_ino < 2**128:
        raise ProjectOperationError("file_integrity_mismatch", "Идентичность файлового объекта недоступна.")
    return ManagedFileIdentity(f"{value.st_dev:016x}", f"{value.st_ino:032x}")


@contextmanager
def open_managed_file_for_discard(path: Path) -> Generator[BinaryIO]:
    descriptor = _open_windows_descriptor(path, 0x80010000, 3, 0x00200080)
    try:
        with io.FileIO(descriptor, "r", closefd=False) as stream:
            yield stream
    finally:
        os.close(descriptor)


def _open_windows_descriptor(path: Path, desired_access: int, creation: int, flags: int) -> int:
    # FILE_SHARE_READ deliberately excludes writes and namespace replacement.
    handle = _winapi.CreateFile(str(path), desired_access, 1, 0, creation, flags, 0)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY if desired_access & 0x40000000 else os.O_RDONLY)
    except OSError:
        _winapi.CloseHandle(handle)
        raise


@contextmanager
def pin_reserved_directory(path: Path, label: str) -> Generator[None]:
    """Keep every directory ordinary and deny replacement until the writer ends."""
    with ExitStack() as handles:
        for directory in (*reversed(path.parents), path):
            before = os.lstat(directory)
            _reject_reparse_point(directory, before, label)
            descriptor = _open_windows_descriptor(directory, 0x80000000, 3, 0x02200000)
            handles.callback(os.close, descriptor)
            opened = os.fstat(descriptor)
            _reject_reparse_point(directory, opened, label)
            if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ProjectOperationError("file_integrity_mismatch", "Каталог назначения изменился при закреплении.")
        yield


@contextmanager
def open_new_managed_file(path: Path) -> Generator[BinaryIO]:
    """CREATE_NEW plus read-only sharing prevents overwrite and writer substitution."""
    handle = _winapi.CreateFile(str(path), 0xC0010000, 1, 0, 1, 0x00200080, 0)
    descriptor: int | None = None
    stream_ready = False
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        # Raw/buffer wrappers never own the descriptor. Even a constructor
        # failure leaves its native identity available for atomic disposal.
        with io.FileIO(descriptor, "r+", closefd=False) as raw, io.BufferedRandom(raw) as stream:
            stream_ready = True
            yield stream
    finally:
        try:
            if not stream_ready:
                _discard_windows_handle(handle)
        finally:
            if descriptor is None:
                _winapi.CloseHandle(handle)
            else:
                os.close(descriptor)


class _NativeBoolean(ctypes.c_int):
    pass


def discard_opened_managed_file(descriptor: int) -> None:
    """Mark this exact CREATE_NEW object for deletion before releasing its handle."""
    _discard_windows_handle(msvcrt.get_osfhandle(descriptor))


def _discard_windows_handle(handle: int) -> None:
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    dispose = library.SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    dispose.restype = _NativeBoolean
    disposition = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN.
    result: object = dispose(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition))
    if not isinstance(result, _NativeBoolean):
        raise OSError("Unexpected SetFileInformationByHandle result")
    if not result.value:
        raise ctypes.WinError(ctypes.get_last_error())


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


def ensure_managed_directory(path: Path, label: str) -> None:
    try:
        path.mkdir(exist_ok=True)
    except OSError as error:
        raise ProjectOperationError("storage_error", f"Managed directory {label} недоступна.") from error
    inspect_reserved_directory(path, label)


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
