import json
import stat
import tempfile
import unittest
from pathlib import Path

from agent_manage.manager_core import ManagerCore


class DummyRunner:
    dry_run = False
    openclaw_bin = "openclaw"

    def log(self, message):
        return None


class ManagerCoreConfigTest(unittest.TestCase):
    def test_config_write_is_atomic_private_and_cleans_failed_temp_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "openclaw.json"
            config_path.write_text('{"before": true}\n', encoding="utf-8")
            manager = ManagerCore(DummyRunner(), config_path=str(config_path))

            manager._write_config({"after": True}, note="test")

            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                {"after": True},
            )
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

            with self.assertRaises(TypeError):
                manager._write_config({"invalid": object()}, note="invalid")

            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                {"after": True},
            )
            self.assertEqual(
                sorted(path.name for path in Path(tmpdir).iterdir()),
                ["openclaw.json", "openclaw.json.bak"],
            )
            self.assertEqual(
                json.loads(
                    (Path(tmpdir) / "openclaw.json.bak").read_text(encoding="utf-8")
                ),
                {"before": True},
            )

    def test_load_config_rejects_non_object_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "openclaw.json"
            config_path.write_text("[]\n", encoding="utf-8")
            manager = ManagerCore(DummyRunner(), config_path=str(config_path))

            with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                manager._load_config()


if __name__ == "__main__":
    unittest.main()
