import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_manage.manager_core import ManagerCore
from agent_manage.models import CreateInstanceRequest
from agent_manage.orchestrator import InstanceManagerV2


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

    def test_container_runtime_defers_gateway_restart_without_systemctl(self):
        runner = DummyRunner()
        with patch.dict(os.environ, {"UNITAG_AGENT_MANAGER_RUNTIME": "container"}):
            manager = InstanceManagerV2(runner)
            result = manager._restart_gateway_service()

        self.assertTrue(manager.restart_required)
        self.assertEqual(result["method"], "container_restart_required")

    def test_container_runtime_uses_persistent_openclaw_paths_and_gateway_token(self):
        runner = DummyRunner()
        with patch.dict(
            os.environ,
            {
                "UNITAG_AGENT_MANAGER_RUNTIME": "container",
                "OPENCLAW_GATEWAY_TOKEN": "container-gateway-token",
            },
            clear=False,
        ):
            manager = ManagerCore(runner)
            self.assertTrue(
                str(manager.template_root)
                .replace("\\", "/")
                .endswith("/home/node/.openclaw/templates")
            )
            self.assertTrue(
                str(manager.config_path)
                .replace("\\", "/")
                .endswith("/home/node/.openclaw/openclaw.json")
            )
            self.assertEqual(manager._container_gateway_token(), "container-gateway-token")

    def test_container_create_instance_writes_environment_gateway_token(self):
        runner = DummyRunner()
        with patch.dict(
            os.environ,
            {
                "UNITAG_AGENT_MANAGER_RUNTIME": "container",
                "OPENCLAW_GATEWAY_TOKEN": "container-gateway-token",
            },
            clear=False,
        ):
            manager = InstanceManagerV2(runner)
            configured_tokens = []
            with (
                patch.object(manager, "resolve_agent_name", return_value="base"),
                patch.object(manager, "resolve_archive_path", return_value=Path("/tmp/base.zip")),
                patch.object(manager, "resolve_template_dir", return_value=Path("/tmp/template/base")),
                patch.object(
                    manager,
                    "_provision_agent_from_template",
                    return_value={
                        "created_agent": True,
                        "created_template_dir": False,
                        "created_workspace": False,
                    },
                ),
                patch.object(manager, "_load_template_manifest", return_value={}),
                patch.object(manager, "_multi_agent_specs_from_template", return_value=[]),
                patch.object(
                    manager,
                    "_fetch_supported_gateway_models",
                    return_value={"models": [], "official_image_model_available": False},
                ),
                patch.object(manager, "_configure_config_models"),
                patch.object(
                    manager,
                    "_configure_gateway_auth",
                    side_effect=lambda token: configured_tokens.append(token) or {},
                ),
                patch.object(manager, "_configure_config_tools"),
                patch.object(manager, "_configure_workspace_defaults"),
                patch.object(manager, "_generate_gateway_token", side_effect=AssertionError),
            ):
                result = manager.create_instance(
                    CreateInstanceRequest(template_name="base", model_key="model-key")
                )

        self.assertEqual(result["gateway_token"], "container-gateway-token")
        self.assertTrue(result["gateway_token_preserved"])
        self.assertEqual(configured_tokens, ["container-gateway-token"])


if __name__ == "__main__":
    unittest.main()
