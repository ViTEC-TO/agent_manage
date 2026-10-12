import base64
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_manage.cli import main
from agent_manage.local import LocalRunner
from agent_manage.orchestrator import InstanceManagerV2
from agent_manage.response import ProtocolError, build_error_response, build_success_response, print_json


class SegmentCliTest(unittest.TestCase):
    def test_arguments_and_response(self):
        with patch.object(InstanceManagerV2, "segment_history", return_value={"messages": []}) as call:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["sessions-segments-history", "--session-key", "agent:main:thread:x",
                                       "--session-id", "old", "--edge", "start", "--limit", "4"]), 0)
            call.assert_called_once_with("agent:main:thread:x", "old", edge="start", limit=4, cursor=None)
            self.assertEqual(json.loads(output.getvalue())["result"], {"messages": []})

    def test_domain_error_code_survives_cli(self):
        with patch.object(InstanceManagerV2, "segment_history", side_effect=ProtocolError("FORBIDDEN", "Denied")):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["sessions-segments-history", "--session-key", "agent:main:thread:x", "--session-id", "old"]), 1)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "FORBIDDEN")

    def test_validation_before_native_process(self):
        manager = InstanceManagerV2(LocalRunner())
        for options in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"edge": "start", "cursor": "abc"}):
            with self.assertRaises(ValueError):
                manager.segment_history("agent:main:main", "old", **options)
        for session_id in ("../secret", "", "a/b"):
            with self.assertRaises(ValueError):
                manager.segment_history("agent:main:main", session_id)


@unittest.skipUnless(os.environ.get("AGENT_MANAGE_TEST_OPENCLAW_BIN"), "Set July OpenClaw for native history tests")
class NativeSegmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-segment-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.binary = os.environ["AGENT_MANAGE_TEST_OPENCLAW_BIN"]
        self.package = Path(self.binary).resolve().parent
        self.key = "agent:example:dashboard:thread-key"
        self.ids = [f"00000000-0000-4000-8000-{number:012}" for number in (1, 2, 3)]
        self.sessions = self.root / "agents/example/sessions"
        self.sessions.mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config = self.root / "openclaw.json"
        self.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "fixture-only-secret"}},
            "agents": {"list": [{"id": "example", "workspace": str(self.workspace)}]}}))
        self.entry = {"sessionId": self.ids[-1], "usageFamilySessionIds": self.ids,
                      "updatedAt": 1791700000000, "displayName": "Keep this title", "model": "fixture-model"}
        self.seed()
        self.paths = []
        for index, session_id in enumerate(self.ids):
            file = self.sessions / (session_id + ".jsonl" + (".reset.2026-10-12T02-00-00.000Z" if index < 2 else ""))
            records = [{"type": "session", "id": session_id, "version": 3, "timestamp": "2026-10-12T01:00:00.000Z"}]
            records += [self.record(f"s{index}-{n}", "user" if n % 2 == 0 else "assistant", "正文" * 1800 if n == 0 else f"message-{n}",
                                    None if n == 0 else f"s{index}-{n-1}") for n in range(7)]
            file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
            self.paths.append(file)
        self.manager = InstanceManagerV2(LocalRunner(self.binary), config_path=str(self.config))

    def seed(self):
        script = """const sdk=await import(process.argv[1]+'/dist/plugin-sdk/session-store-runtime.js');
await sdk.saveSessionStore(process.argv[2], JSON.parse(process.argv[3]), {skipMaintenance:true});"""
        self.env = dict(os.environ, OPENCLAW_STATE_DIR=str(self.root), OPENCLAW_CONFIG_PATH=str(self.config))
        subprocess.run(["node", "--input-type=module", "-e", script, str(self.package),
                        str(self.sessions / "sessions.json"), json.dumps({self.key: self.entry})],
                       env=self.env, capture_output=True, check=True)

    @staticmethod
    def record(id, role, text, parent=None):
        return {"type": "message", "id": id, "parentId": parent, "timestamp": "2026-10-12T01:00:01.000Z",
                "message": {"role": role, "content": [{"type": "text", "text": text}], "timestamp": 1791766801000}}

    def append(self, record, index=0):
        with self.paths[index].open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    def history(self, index=0, **options):
        return self.manager.segment_history(self.key, self.ids[index], **options)

    def test_three_segments_bidirectional_full_text_and_read_only(self):
        before = {file: file.read_bytes() for file in [self.config, *self.paths]}
        # Compare both July JSON session indexes and any native SQLite stores byte-for-byte.
        before.update({file: file.read_bytes() for file in self.root.rglob("sessions.json")})
        database = list(self.root.rglob("*.sqlite"))
        before.update({file: file.read_bytes() for file in database})
        for index in range(3):
            page = self.history(index, edge="start", limit=2)
            self.assertEqual(page["messages"][0]["content"][0]["text"], "正文" * 1800)
            self.assertEqual(page["segment"]["isCurrent"], index == 2)
            self.assertLessEqual(len(page["segment"]["preview"][0]), 180)
            forward = list(page["messages"])
            while page["newerCursor"]:
                page = self.history(index, cursor=page["newerCursor"], limit=2)
                forward.extend(page["messages"])
            self.assertEqual([m["id"] for m in forward], [f"s{index}-{n}" for n in range(7)])
            page = self.history(index, edge="end", limit=3)
            backward = list(page["messages"])
            while page["olderCursor"]:
                page = self.history(index, cursor=page["olderCursor"], limit=3)
                backward = page["messages"] + backward
            self.assertEqual([m["id"] for m in backward], [m["id"] for m in forward])
        for file, content in before.items():
            self.assertEqual(file.read_bytes(), content, str(file))

    def test_tools_thinking_images_and_protected_media_chunks(self):
        image = self.workspace / "image.png"
        image.write_bytes(b"fixture-image" * 20000)
        record = self.record("tools", "assistant", "MEDIA:" + str(image), "s0-6")
        record["message"]["content"] += [{"type": "thinking", "thinking": "Saved reasoning"},
          {"type": "toolCall", "id": "call1", "name": "draw", "arguments": {"prompt": "完整参数"}},
          {"type": "image", "mimeType": "image/png", "data": base64.b64encode(b"inline-image").decode()}]
        self.append(record)
        result = self.record("result", "toolResult", "tool-result" * 2000, "tools")
        result["message"]["toolCallId"] = "call1"
        self.append(result)
        page = self.history(limit=2)
        message = page["messages"][0]
        self.assertEqual(message["content"][1]["thinking"], "Saved reasoning")
        self.assertEqual(message["content"][2]["arguments"], {"prompt": "完整参数"})
        self.assertEqual(page["messages"][1]["content"][0]["text"], "tool-result" * 2000)
        for reference, expected in zip(message["mediaRefs"], (image.read_bytes(), b"inline-image")):
            params = reference["params"]
            part = self.manager.segment_media_get(self.key, self.ids[0], params["ref"])
            content = base64.b64decode(part["data"])
            while part["nextCursor"]:
                part = self.manager.segment_media_get(self.key, self.ids[0], params["ref"], cursor=part["nextCursor"])
                content += base64.b64decode(part["data"])
            self.assertEqual(content, expected)

    def test_saved_media_metadata_relative_paths_and_file_urls(self):
        image = self.workspace / "image with spaces.png"
        image.write_bytes(b"image-with-space")
        record = self.record("metadata", "user", "Image", "s0-6")
        record["message"]["media"] = [{"path": "image with spaces.png", "contentType": "image/png"}]
        record["message"]["details"] = {"mediaUrls": [image.as_uri()]}
        self.append(record)
        message = self.history(limit=1)["messages"][0]
        self.assertEqual(message["media"], record["message"]["media"])
        for ref in message["mediaRefs"]:
            result = self.manager.segment_media_get(self.key, self.ids[0], ref["params"]["ref"])
            self.assertEqual(base64.b64decode(result["data"]), image.read_bytes())

    def test_media_symlink_missing_and_changed_chunk(self):
        target = self.workspace / "target.png"
        target.write_bytes(b"target")
        link = self.workspace / "link.png"
        link.symlink_to(target)
        self.append(self.record("linked", "assistant", "MEDIA:" + str(link), "s0-6"))
        ref = self.history(limit=1)["messages"][0]["mediaRefs"][0]["params"]["ref"]
        with self.assertRaises(ProtocolError) as error:
            self.manager.segment_media_get(self.key, self.ids[0], ref)
        self.assertEqual(error.exception.code, "FORBIDDEN")
        self.append(self.record("target", "assistant", "MEDIA:" + str(target), "linked"))
        target.write_bytes(b"a" * 150000)
        ref = self.history(limit=1)["messages"][0]["mediaRefs"][0]["params"]["ref"]
        part = self.manager.segment_media_get(self.key, self.ids[0], ref)
        target.write_bytes(b"b" * 150000)
        with self.assertRaises(ProtocolError) as error:
            self.manager.segment_media_get(self.key, self.ids[0], ref, cursor=part["nextCursor"])
        self.assertEqual(error.exception.code, "CURSOR_EXPIRED")
        target.unlink()
        with self.assertRaises(ProtocolError) as error:
            self.manager.segment_media_get(self.key, self.ids[0], ref)
        self.assertEqual(error.exception.code, "MEDIA_NOT_FOUND")

    def test_oversized_message_can_be_reassembled_within_output_budget(self):
        text = "完整长正文🙂" * 70000
        self.append(self.record("large", "assistant", text, "s0-6"))
        page = self.history(limit=1)
        message = page["messages"][0]
        self.assertTrue(message["__openclaw"]["truncated"])
        ref = message["fullMessageRef"]["params"]["ref"]
        part = self.manager.segment_message_get(self.key, self.ids[0], "large", ref=ref)
        content = b""
        while True:
            output = io.StringIO()
            with redirect_stdout(output):
                print_json(build_success_response(part))
            self.assertIsNone(json.loads(output.getvalue())["error"])
            content += base64.b64decode(part["data"])
            if not part["nextCursor"]:
                break
            part = self.manager.segment_message_get(self.key, self.ids[0], "large", cursor=part["nextCursor"])
        self.assertEqual(json.loads(content)["content"][0]["text"], text)

    def test_snapshot_survives_append_and_reset_rename(self):
        page = self.history(2, edge="start", limit=2)
        self.append(self.record("new", "assistant", "new message", "s2-6"), 2)
        self.paths[2].rename(self.paths[2].with_name(self.paths[2].name + ".reset.2026-10-12T03-00-00.000Z"))
        new_id = "00000000-0000-4000-8000-000000000004"
        self.entry.update(sessionId=new_id, usageFamilySessionIds=[*self.ids, new_id])
        self.seed()
        messages = list(page["messages"])
        while page["newerCursor"]:
            page = self.history(2, cursor=page["newerCursor"], limit=2)
            messages.extend(page["messages"])
        self.assertEqual([m["id"] for m in messages], [f"s2-{n}" for n in range(7)])
        self.assertFalse(page["segment"]["isCurrent"])

    def test_forbidden_missing_expired_and_empty_are_distinct(self):
        with self.assertRaises(ProtocolError) as error:
            self.manager.segment_history(self.key, "other-thread-id")
        self.assertEqual(error.exception.code, "FORBIDDEN")
        cursor = self.history(limit=2)["olderCursor"]
        with self.assertRaises(ProtocolError) as error:
            self.history(1, cursor=cursor)
        self.assertEqual(error.exception.code, "CURSOR_EXPIRED")
        self.paths[0].unlink()
        with self.assertRaises(ProtocolError) as error:
            self.history()
        self.assertEqual(error.exception.code, "SEGMENT_NOT_FOUND")
        with self.assertRaises(ProtocolError) as error:
            self.history(cursor=cursor)
        self.assertEqual(error.exception.code, "CURSOR_EXPIRED")
        self.paths[0].write_text(json.dumps({"type": "session", "id": self.ids[0]}) + "\n")
        empty = self.history()
        self.assertEqual(empty["messages"], [])
        self.assertIsNone(empty["olderCursor"])
        self.assertIsNone(empty["newerCursor"])

    def test_rewritten_snapshot_and_media_escape_rejected(self):
        cursor = self.history(limit=2)["olderCursor"]
        self.paths[0].write_text(self.paths[0].read_text().replace("message-1", "rewritten"))
        with self.assertRaises(ProtocolError) as error:
            self.history(cursor=cursor)
        self.assertEqual(error.exception.code, "CURSOR_EXPIRED")
        outside = self.root / "private.png"
        outside.write_bytes(b"not-media-root")
        self.append(self.record("escape", "assistant", "MEDIA:" + str(outside), "s0-6"))
        ref = self.history(limit=1)["messages"][0]["mediaRefs"][0]["params"]["ref"]
        with self.assertRaises(ProtocolError) as error:
            self.manager.segment_media_get(self.key, self.ids[0], ref)
        self.assertEqual(error.exception.code, "FORBIDDEN")

    def test_native_branch_selection_and_partial_tail(self):
        self.append(self.record("branch-a", "assistant", "selected", "s2-6"), 2)
        self.append(self.record("branch-b", "assistant", "abandoned", "s2-6"), 2)
        self.append({"type": "leaf", "id": "selector", "parentId": "branch-b", "targetId": "branch-a"}, 2)
        with self.paths[2].open("a") as stream:
            stream.write('{"type":"message"')
        messages = self.history(2, limit=30)["messages"]
        self.assertIn("branch-a", [m["id"] for m in messages])
        self.assertNotIn("branch-b", [m["id"] for m in messages])

    def test_archive_without_newline_preserves_last_record(self):
        self.paths[0].write_text(self.paths[0].read_text().rstrip("\n"))
        page = self.history(edge="end", limit=1)
        self.assertEqual(page["messages"][0]["id"], "s0-6")
        page = self.history(cursor=page["olderCursor"], limit=1)
        self.assertEqual(page["messages"][0]["id"], "s0-5")


if __name__ == "__main__":
    unittest.main()
