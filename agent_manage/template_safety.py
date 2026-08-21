"""Path and archive safety rules for agent templates."""

from __future__ import annotations

import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def validate_safe_name(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if (
        normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"Invalid {field} '{value}': expected a single path-safe name")
    return normalized


def require_path_within(path: Path, root: Path, field: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"{field} escapes template root: {path}")
    return resolved_path


def validate_template_archive(archive_path: Path) -> None:
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            _validate_archive_limits(
                member_count=len(members),
                total_size=sum(member.file_size for member in members),
            )
            for member in members:
                mode = member.external_attr >> 16
                _validate_archive_member(
                    member.filename,
                    is_link=stat.S_ISLNK(mode),
                )
        return

    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as archive:
            members = archive.getmembers()
            _validate_archive_limits(
                member_count=len(members),
                total_size=sum(member.size for member in members),
            )
            for member in members:
                _validate_archive_member(
                    member.name,
                    is_link=not (member.isfile() or member.isdir()),
                )
        return

    raise ValueError(f"Unsupported template archive format: {archive_path}")


def _validate_archive_limits(*, member_count: int, total_size: int) -> None:
    if member_count > MAX_ARCHIVE_MEMBERS:
        raise ValueError(
            f"Template archive has too many members: {member_count} "
            f"(max {MAX_ARCHIVE_MEMBERS})"
        )
    if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"Template archive is too large after extraction: {total_size} bytes "
            f"(max {MAX_ARCHIVE_UNCOMPRESSED_BYTES})"
        )


def _validate_archive_member(name: str, *, is_link: bool) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise ValueError(f"Unsafe template archive member path: {name}")
    if is_link:
        raise ValueError(f"Template archive links are not allowed: {name}")
