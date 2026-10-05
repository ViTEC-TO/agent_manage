"""Secure, rollback-safe retrieval of template archives."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .template_safety import validate_template_archive

TEMPLATE_DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_TEMPLATE_DOWNLOAD_BYTES = 256 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def validate_template_url(url: str) -> str:
    resolved = url.strip()
    if not resolved or any(ord(character) < 32 for character in resolved):
        raise ValueError("Invalid template_zip_url")
    parsed = urlsplit(resolved)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("template_zip_url must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("template_zip_url credentials are not allowed")
    if parsed.fragment:
        raise ValueError("template_zip_url fragments are not allowed")
    if "\\" in parsed.netloc or any(character.isspace() for character in parsed.netloc):
        raise ValueError("template_zip_url hostname is invalid")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("template_zip_url port is invalid") from exc
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("template_zip_url localhost is not allowed")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("template_zip_url IP literals are not allowed")
    return resolved


def normalize_sha256(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    resolved = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", resolved):
        raise ValueError("template_zip_sha256 must contain 64 hexadecimal characters")
    return resolved


class SafeTemplateRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_template_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_template_archive(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    timeout_seconds: int = TEMPLATE_DOWNLOAD_TIMEOUT_SECONDS,
    max_bytes: int = MAX_TEMPLATE_DOWNLOAD_BYTES,
) -> Path:
    resolved_url = validate_template_url(url)
    resolved_sha256 = normalize_sha256(expected_sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.download-",
        dir=str(destination.parent),
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        opener = build_opener(ProxyHandler(), SafeTemplateRedirectHandler())
        request = Request(
            resolved_url,
            headers={
                "Accept": "application/zip, application/octet-stream",
                "User-Agent": "unitag-agent-manager/0.5",
            },
        )
        with opener.open(request, timeout=timeout_seconds) as response, os.fdopen(fd, "wb") as output:
            fd = -1
            validate_template_url(response.geturl())
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise ValueError("Template response Content-Length is invalid") from exc
                if declared_length < 0 or declared_length > max_bytes:
                    raise ValueError("Template archive exceeds the download size limit")
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Template archive exceeds the download size limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual_sha256 = digest.hexdigest()
        if resolved_sha256 is not None and actual_sha256 != resolved_sha256:
            raise ValueError("Template archive SHA-256 mismatch")
        validate_template_archive(temporary_path)
        return temporary_path
    except Exception:
        if fd >= 0:
            os.close(fd)
        temporary_path.unlink(missing_ok=True)
        raise


@contextmanager
def replace_template_archive(
    *,
    url: str | None,
    expected_sha256: str | None,
    destination: Path,
) -> Iterator[None]:
    if not url:
        if expected_sha256:
            raise ValueError("template_zip_sha256 requires template_zip_url")
        yield
        return

    temporary_path = download_template_archive(
        url,
        destination,
        expected_sha256=expected_sha256,
    )
    backup_path: Path | None = None
    try:
        if destination.exists():
            if not destination.is_file():
                raise ValueError(f"Template archive path is not a file: {destination}")
            backup_fd, backup_name = tempfile.mkstemp(
                prefix=f".{destination.name}.backup-",
                dir=str(destination.parent),
            )
            os.close(backup_fd)
            backup_path = Path(backup_name)
            backup_path.unlink()
            destination.replace(backup_path)
        try:
            temporary_path.replace(destination)
        except Exception:
            if backup_path is not None and backup_path.exists():
                backup_path.replace(destination)
            raise
        try:
            yield
        except Exception:
            destination.unlink(missing_ok=True)
            if backup_path is not None and backup_path.exists():
                backup_path.replace(destination)
            raise
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
    finally:
        temporary_path.unlink(missing_ok=True)
