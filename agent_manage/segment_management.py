"""Read-only physical transcript segments for a logical OpenClaw thread."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .response import ProtocolError


class SegmentManagementMixin:
    def segment_history(self, session_key, session_id, *, edge=None, limit=30, cursor=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if edge is not None and cursor is not None:
            raise ValueError("edge and cursor are mutually exclusive")
        if edge is not None and edge not in ("start", "end"):
            raise ValueError("edge must be start or end")
        return self._segment_bridge("history", session_key, session_id,
                                    edge=edge or "end", limit=limit, cursor=cursor)

    def segment_message_get(self, session_key, session_id, message_id, *, ref=None, cursor=None):
        return self._segment_bridge("message", session_key, session_id,
                                    messageId=message_id, ref=ref, cursor=cursor)

    def segment_media_get(self, session_key, session_id, ref=None, *, cursor=None):
        if ref is None and cursor is None:
            raise ValueError("media read requires ref or cursor")
        return self._segment_bridge("media", session_key, session_id, ref=ref, cursor=cursor)

    def _segment_bridge(self, action, session_key, session_id, **params):
        if not isinstance(session_key, str) or not re.fullmatch(r"agent:[a-zA-Z0-9_-]+:.+", session_key):
            raise ValueError("session-key must be a full agent session key")
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", session_id):
            raise ValueError("Invalid physical session ID")
        if action == "message" and (not isinstance(params["messageId"], str) or not params["messageId"]):
            raise ValueError("message-id is required")
        for key in ("cursor", "ref"):
            value = params.get(key)
            if value is not None and (not isinstance(value, str) or not value or len(value) > 8192):
                raise ValueError(f"Invalid {key}")
        env = self.runner._command_env()
        binary = shutil.which(self.bin, path=env.get("PATH"))
        node = shutil.which("node", path=env.get("PATH"))
        if not binary or not node:
            raise FileNotFoundError("OpenClaw and Node.js are required for segment history")
        package = Path(binary).resolve().parent
        info = json.loads((package / "package.json").read_text())
        if info.get("name") != "openclaw" or not re.fullmatch(r"2026\.7\.[1-9][0-9]*(?:-[1-9][0-9]*)?", str(info.get("version", ""))):
            raise ValueError("Segment history currently supports server OpenClaw 2026.7.x only")
        config = self._load_config()
        payload = {"action": action, "sessionKey": session_key, "sessionId": session_id,
                   "config": config, "root": str(self.config_path.parent), "package": str(package), **params}
        env.update(OPENCLAW_STATE_DIR=str(self.config_path.parent), OPENCLAW_CONFIG_PATH=str(self.config_path))
        try:
            completed = subprocess.run([node, str(Path(__file__).with_name("segment_history_bridge.mjs"))],
                                       input=json.dumps(payload), text=True, capture_output=True,
                                       env=env, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            raise ProtocolError("SEGMENT_READ_FAILED", "Segment read timed out", 20) from None
        # Native modules may log diagnostics. Never include transcript/config stdout in errors.
        rows = [line[len("AGENT_SEGMENT_RESULT:"):] for line in completed.stdout.splitlines()
                if line.startswith("AGENT_SEGMENT_RESULT:")]
        if completed.returncode or len(rows) != 1:
            raise ProtocolError("SEGMENT_READ_FAILED", "OpenClaw segment reader failed", 20)
        result = json.loads(rows[0])
        if not result.get("ok"):
            error = result["error"]
            code = error["code"]
            raise ProtocolError(code, error["message"], 10 if code in ("SEGMENT_NOT_FOUND", "MEDIA_NOT_FOUND", "MESSAGE_NOT_FOUND") else 12 if code == "CURSOR_EXPIRED" else 20 if code == "SEGMENT_READ_FAILED" else 11)
        return result["payload"]
