import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from agent_manage.template_download import (
    SafeTemplateRedirectHandler,
    download_template_archive,
    replace_template_archive,
    validate_template_url,
)


def build_zip_bytes(content: str = "new") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("AGENTS.md", content)
    return output.getvalue()


class FakeResponse:
    def __init__(self, content: bytes, url: str, content_length: str | None = None):
        self.stream = io.BytesIO(content)
        self.url = url
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int) -> bytes:
        return self.stream.read(size)

    def geturl(self) -> str:
        return self.url


class FakeOpener:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.timeout = None

    def open(self, request, timeout):
        self.timeout = timeout
        return self.response


class TemplateDownloadTest(unittest.TestCase):
    def test_url_rejects_unsafe_targets(self):
        for value in (
            "http://assets.example.test/a.zip",
            "https://user:pass@assets.example.test/a.zip",
            "https://assets.example.test/a.zip#fragment",
            "https://localhost/a.zip",
            "https://sub.localhost/a.zip",
            "https://127.0.0.1/a.zip",
            "https://[::1]/a.zip",
            "https://assets.example.test\\@localhost/a.zip",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_template_url(value)

    def test_redirect_revalidates_target(self):
        handler = SafeTemplateRedirectHandler()
        with self.assertRaises(ValueError):
            handler.redirect_request(None, None, 302, "Found", {}, "http://example.test/a.zip")

    def test_download_streams_valid_archive_and_uses_proxy_handler(self):
        content = build_zip_bytes()
        opener = FakeOpener(
            FakeResponse(content, "https://cdn.example.test/team.zip", str(len(content)))
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://proxy.example.test:8080"},
            clear=True,
        ), patch(
            "agent_manage.template_download.build_opener", return_value=opener
        ) as build:
            destination = Path(tmpdir) / "team.zip"
            temporary = download_template_archive(
                "https://assets.example.test/team.zip",
                destination,
                expected_sha256=hashlib.sha256(content).hexdigest(),
            )
            try:
                self.assertEqual(temporary.read_bytes(), content)
                self.assertEqual(temporary.parent, destination.parent)
                self.assertEqual(opener.timeout, 60)
                self.assertEqual(type(build.call_args.args[0]).__name__, "ProxyHandler")
                self.assertEqual(
                    build.call_args.args[0].proxies["https"],
                    "http://proxy.example.test:8080",
                )
            finally:
                temporary.unlink(missing_ok=True)

    def test_download_rejects_digest_mismatch_without_destination_change(self):
        content = build_zip_bytes()
        opener = FakeOpener(FakeResponse(content, "https://assets.example.test/team.zip"))
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agent_manage.template_download.build_opener", return_value=opener
        ):
            destination = Path(tmpdir) / "team.zip"
            destination.write_bytes(b"existing")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                download_template_archive(
                    "https://assets.example.test/team.zip",
                    destination,
                    expected_sha256="0" * 64,
                )
            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertEqual(list(destination.parent.glob(".team.zip.download-*")), [])

    def test_download_enforces_streamed_size_limit(self):
        content = build_zip_bytes("x" * 1024)
        opener = FakeOpener(FakeResponse(content, "https://assets.example.test/team.zip"))
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agent_manage.template_download.build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(ValueError, "size limit"):
                download_template_archive(
                    "https://assets.example.test/team.zip",
                    Path(tmpdir) / "team.zip",
                    max_bytes=32,
                )

    def test_download_rejects_invalid_archive_before_replacement(self):
        content = b"not a zip"
        opener = FakeOpener(FakeResponse(content, "https://assets.example.test/team.zip"))
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agent_manage.template_download.build_opener", return_value=opener
        ):
            destination = Path(tmpdir) / "team.zip"
            destination.write_bytes(b"existing")
            with self.assertRaisesRegex(ValueError, "Unsupported template archive"):
                download_template_archive(
                    "https://assets.example.test/team.zip",
                    destination,
                )
            self.assertEqual(destination.read_bytes(), b"existing")

    def test_replace_rolls_back_existing_archive_when_body_fails(self):
        old_content = build_zip_bytes("old")
        new_content = build_zip_bytes("new")
        opener = FakeOpener(FakeResponse(new_content, "https://assets.example.test/team.zip"))
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agent_manage.template_download.build_opener", return_value=opener
        ):
            destination = Path(tmpdir) / "team.zip"
            destination.write_bytes(old_content)
            with self.assertRaisesRegex(RuntimeError, "provision failed"):
                with replace_template_archive(
                    url="https://assets.example.test/team.zip",
                    expected_sha256=hashlib.sha256(new_content).hexdigest(),
                    destination=destination,
                ):
                    self.assertEqual(destination.read_bytes(), new_content)
                    raise RuntimeError("provision failed")
            self.assertEqual(destination.read_bytes(), old_content)
            self.assertEqual(list(destination.parent.glob(".team.zip.backup-*")), [])


if __name__ == "__main__":
    unittest.main()
