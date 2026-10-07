import io
import fcntl
import json
import shutil
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_manage.cli import main
from agent_manage.local import CommandResult, LocalRunner
from agent_manage.models import RefreshAgentRequest
from agent_manage.orchestrator import InstanceManagerV2
from agent_manage.response import TYPE_CODE_CONFLICT, TYPE_CODE_OPERATION_ROLLED_BACK, build_error_response


class RefreshRunner:
    openclaw_bin = "openclaw"
    dry_run = False

    def __init__(self):
        self.calls = []
        self.fail_validate = False
        self.fail_health_once = False
        self.scope_mismatch = False

    def log(self, message):
        pass

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[1:] == ["config", "validate"]:
            if self.fail_validate:
                raise RuntimeError("invalid config")
            json.loads(Path(kwargs["env_overrides"]["OPENCLAW_CONFIG_PATH"]).read_text())
        if args[1:] == ["gateway", "status", "--json"]:
            config_path = kwargs["env_overrides"]["OPENCLAW_CONFIG_PATH"]
            return CommandResult(args, "test", 0, json.dumps({"config": {"daemon": {"path": config_path}, "mismatch": self.scope_mismatch}, "service": {"loaded": True, "command": {"environment": {"OPENCLAW_CONFIG_PATH": config_path}}}}), "")
        if "status" in args:
            if self.fail_health_once:
                self.fail_health_once = False
                return CommandResult(args, "test", 0, '{"rpc":{"ok":false}}', "")
            return CommandResult(args, "test", 0, '{"rpc":{"ok":true}}', "")
        return CommandResult(args, "test", 0, "", "")


class RefreshManagementTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config_path = self.root / "env" / "openclaw.json"
        self.config_path.parent.mkdir()
        self.config = {
            "agents": {"list": [{"id": "demo", "workspace": str(self.workspace), "model": {"primary": "dola/chat"}}],
                       "defaults": {"model": {"primary": "dola/chat", "fallbacks": ["custom/mine"]},
                                    "imageGenerationModel": {"primary": "openai/gpt-image-2", "timeoutMs": 180000},
                                    "models": {"dola/chat": {"alias": "keep"}, "custom/mine": {"alias": "custom"}}}},
            "models": {"mode": "merge", "providers": {
                "custom": {"apiKey": "custom-key", "baseUrl": "https://custom.example/v1", "models": [{"id": "mine"}]},
                "dola": {"apiKey": {"source": "env", "provider": "default", "id": "DOLA_KEY"},
                         "api": "openai-completions", "baseUrl": "https://api.dola.io/aigateway/mystore/v1", "models": [{"id": "chat"}]}}},
            "tools": {"web": {"search": {"enabled": True}}},
            "channels": {"telegram": {"accounts": {"main": {"botToken": "preserve"}}}},
            "bindings": [{"agentId": "demo", "match": {"channel": "telegram"}}],
            "gateway": {"auth": {"token": "preserve"}},
        }
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o640)
        self.source = self.root / "template"
        self.source.mkdir()
        self.write_source("template.yaml", "version: 1.0.0\n")
        self.write_source("SOUL.md", "version one\n")
        self.write_source("MEMORY.md", "template initial memory\n")
        self.write_source("memory/day.md", "template day\n")
        self.write_source("USER.md", "template preference\n")
        (self.workspace / "MEMORY.md").write_text("user memory\n")
        (self.workspace / "memory").mkdir()
        (self.workspace / "memory/day.md").write_text("user day\n")
        self.runner = RefreshRunner()
        self.manager = InstanceManagerV2(self.runner, config_path=str(self.config_path), template_root=str(self.root / "cache"))
        self.catalog = {"models": [{"id": "chat", "provider": "dola", "model_ref": "dola/chat", "definition": {"id": "chat", "name": "Chat"}},
                                   {"id": "new", "provider": "dola", "model_ref": "dola/new", "definition": {"id": "new", "name": "New"}}],
                        "models_config": {"providers": {"dola": {"api": "openai-completions", "models": [{"id": "chat", "name": "Chat"}, {"id": "new", "name": "New"}]}}}}
        self.fetcher = patch.object(self.manager, "_fetch_supported_gateway_models", return_value=self.catalog).start()
        self.addCleanup(patch.stopall)

    def write_source(self, relative, text):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def request(self, **kwargs):
        return RefreshAgentRequest("demo", template_dir=str(self.source), template_only=True, **kwargs)

    def state(self):
        return json.loads((self.config_path.parent / "agent-manage/agents/demo.json").read_text())

    def test_template_refresh_records_real_version_and_preserves_memory(self):
        before = self.config_path.read_bytes()
        result = self.manager.refresh_agent(self.request())
        self.assertIsNone(result["template"]["version_before"])
        self.assertEqual(result["template"]["version_after"], "1.0.0")
        self.assertEqual(self.state()["version"], "1.0.0")
        self.assertTrue(Path(self.state()["baseline_path"]).is_dir())
        self.assertEqual((self.workspace / "MEMORY.md").read_text(), "user memory\n")
        self.assertEqual((self.workspace / "memory/day.md").read_text(), "user day\n")
        self.assertFalse((self.workspace / "USER.md").exists())
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse(result["restart_required"])
        self.assertFalse(result["activation_verified"])
        backup = Path(result["backup_path"])
        self.assertEqual((backup / "openclaw.json").read_bytes(), before)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((backup / "openclaw.json").stat().st_mode), 0o600)
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["status"], "complete")

    def test_known_baseline_updates_deletes_owned_files_and_keeps_user_files(self):
        self.write_source("skills/weather/old.txt", "old")
        self.manager.refresh_agent(self.request())
        (self.workspace / "skills/weather/user.txt").write_text("user")
        self.write_source("template.yaml", "version: 2.0.0\n")
        self.write_source("SOUL.md", "version two\n")
        (self.source / "skills/weather/old.txt").unlink()
        result = self.manager.refresh_agent(self.request())
        self.assertEqual(result["template"]["version_before"], "1.0.0")
        self.assertEqual((self.workspace / "SOUL.md").read_text(), "version two\n")
        self.assertFalse((self.workspace / "skills/weather/old.txt").exists())
        self.assertEqual((self.workspace / "skills/weather/user.txt").read_text(), "user")
        self.assertEqual(self.state()["version"], "2.0.0")

    def test_unknown_baseline_conflict_changes_nothing_until_explicit_override(self):
        (self.workspace / "SOUL.md").write_text("custom soul")
        with self.assertRaises(FileExistsError) as caught:
            self.manager.refresh_agent(self.request())
        response = build_error_response(caught.exception)
        self.assertEqual(response["typeCode"], TYPE_CODE_CONFLICT)
        self.assertEqual(response["error"]["details"]["conflicts"][0]["reason"], "no_baseline")
        self.assertFalse((self.workspace / "template.yaml").exists())
        self.assertFalse((self.config_path.parent / "agent-manage/agents/demo.json").exists())
        result = self.manager.refresh_agent(self.request(replace_modified=True))
        manifest = json.loads((Path(result["backup_path"]) / "manifest.json").read_text())
        entry = next(item for item in manifest["files"] if item["path"].endswith("SOUL.md"))
        self.assertEqual((Path(result["backup_path"]) / entry["backup_file"]).read_text(), "custom soul")
        self.assertEqual((self.workspace / "MEMORY.md").read_text(), "user memory\n")

    def test_modified_or_user_deleted_file_conflicts_only_when_template_changes(self):
        self.manager.refresh_agent(self.request())
        (self.workspace / "SOUL.md").unlink()
        result = self.manager.refresh_agent(self.request())
        self.assertIn(str(self.workspace / "SOUL.md"), result["template"]["local_overrides"])
        self.assertFalse((self.workspace / "SOUL.md").exists())
        self.write_source("SOUL.md", "new soul")
        with self.assertRaises(FileExistsError):
            self.manager.refresh_agent(self.request())
        self.assertEqual(self.state()["version"], "1.0.0")

    def test_missing_memory_is_not_reseeded_even_with_replace(self):
        (self.workspace / "MEMORY.md").unlink()
        shutil.rmtree(self.workspace / "memory")
        self.manager.refresh_agent(self.request(replace_modified=True))
        self.assertFalse((self.workspace / "MEMORY.md").exists())
        self.assertFalse((self.workspace / "memory").exists())

    def test_model_refresh_preserves_current_model_custom_provider_secrets_channels_and_tools(self):
        result = self.manager.refresh_agent(RefreshAgentRequest("demo", models_only=True))
        new = json.loads(self.config_path.read_text())
        self.assertEqual(new["agents"]["defaults"]["model"], self.config["agents"]["defaults"]["model"])
        self.assertEqual(new["agents"]["list"], self.config["agents"]["list"])
        self.assertEqual(new["models"]["providers"]["custom"], self.config["models"]["providers"]["custom"])
        self.assertEqual(new["models"]["providers"]["dola"]["apiKey"], self.config["models"]["providers"]["dola"]["apiKey"])
        for field in ("tools", "channels", "bindings", "gateway"):
            self.assertEqual(new[field], self.config[field])
        self.assertEqual(new["agents"]["defaults"]["models"]["dola/chat"], {"alias": "keep"})
        self.assertIn("dola/new", new["agents"]["defaults"]["models"])
        self.assertEqual(new["agents"]["defaults"]["imageGenerationModel"], self.config["agents"]["defaults"]["imageGenerationModel"])
        self.assertNotIn("mediaModels", new["agents"]["defaults"])
        self.assertEqual(result["models"]["scope"], "environment")
        self.fetcher.assert_called_once_with("https://api.dola.io/aigateway/api/frontend/aimodels/byProvider/mystore")
        self.assertFalse((self.config_path.parent / "agent-manage/agents/demo.json").exists())

    def test_removed_selected_model_aborts_without_switching(self):
        self.catalog["models_config"]["providers"]["dola"]["models"] = [{"id": "new"}]
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Selected models absent"):
            self.manager.refresh_agent(RefreshAgentRequest("demo", models_only=True))
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_default_refresh_updates_both_in_one_transaction(self):
        result = self.manager.refresh_agent(RefreshAgentRequest("demo", template_dir=str(self.source)))
        self.assertFalse(result["models"]["skipped"])
        self.assertFalse(result["template"]["skipped"])
        self.assertIn("dola/new", json.loads(self.config_path.read_text())["agents"]["defaults"]["models"])
        self.assertEqual(self.state()["version"], "1.0.0")

    def test_default_refresh_never_calls_gateway_or_requires_restart(self):
        for scope in ("both", "models_only", "template_only"):
            for dry_run in (False, True):
                with self.subTest(scope=scope, dry_run=dry_run):
                    self.runner.calls.clear()
                    self.runner.dry_run = dry_run
                    request = (RefreshAgentRequest("demo", models_only=True)
                               if scope == "models_only" else
                               RefreshAgentRequest("demo", template_dir=str(self.source),
                                                   template_only=scope == "template_only"))
                    self.write_source("SOUL.md", f"updated {scope} {dry_run}\n")
                    self.catalog["models_config"]["providers"]["dola"]["models"][1]["name"] = f"New {scope} {dry_run}"
                    result = self.manager.refresh_agent(request)
                    self.assertFalse(result["restart_required"])
                    self.assertFalse(result["gateway_restarted"])
                    self.assertFalse(result["activation_verified"])
                    self.assertFalse(any("gateway" in args for args, _ in self.runner.calls))
                    if not dry_run:
                        self.assertEqual(self.runner.calls[0][0], ["openclaw", "config", "validate"])

    def test_validate_failure_precedes_live_writes(self):
        self.runner.fail_validate = True
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "invalid config"):
            self.manager.refresh_agent(self.request())
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse((self.workspace / "SOUL.md").exists())
        self.assertEqual(list((self.config_path.parent / "agent-manage/backups").iterdir()), [])

    def test_mid_write_failure_rolls_back_existing_files_and_removes_added_files(self):
        self.write_source("B/obsolete.txt", "restore deletion")
        (self.source / "SOUL.md").chmod(0o755)
        self.manager.refresh_agent(self.request())
        old_state = self.state()
        old_soul = (self.workspace / "SOUL.md").read_bytes()
        self.write_source("SOUL.md", "new soul")
        self.write_source("template.yaml", "version: 2.0.0\n")
        self.write_source("A/new.txt", "new file")
        (self.source / "B/obsolete.txt").unlink()
        real_replace = __import__("os").replace

        def fail_once(source, target):
            if Path(target) == self.workspace / "template.yaml" and not getattr(fail_once, "failed", False):
                fail_once.failed = True
                raise OSError("simulate disk failure")
            return real_replace(source, target)

        with patch("agent_manage.refresh_management.os.replace", side_effect=fail_once):
            with self.assertRaises(RuntimeError) as caught:
                self.manager.refresh_agent(self.request())
        response = build_error_response(caught.exception)
        self.assertEqual(response["typeCode"], TYPE_CODE_OPERATION_ROLLED_BACK)
        self.assertTrue(response["error"]["details"]["rollback_ok"])
        self.assertEqual((self.workspace / "SOUL.md").read_bytes(), old_soul)
        self.assertEqual(stat.S_IMODE((self.workspace / "SOUL.md").stat().st_mode), 0o755)
        self.assertEqual((self.workspace / "B/obsolete.txt").read_text(), "restore deletion")
        self.assertEqual(self.state(), old_state)
        self.assertFalse((self.workspace / "A").exists())

    def test_failed_health_restores_config_files_state_and_old_runtime(self):
        before = self.config_path.read_bytes()
        self.runner.fail_health_once = True
        with self.assertRaises(RuntimeError) as caught:
            self.manager.refresh_agent(RefreshAgentRequest("demo", template_dir=str(self.source), restart=True))
        details = json.loads(str(caught.exception))["details"]
        self.assertTrue(details["rollback_ok"])
        self.assertTrue(details["runtime_restored"])
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.config_path.stat().st_mode), 0o640)
        self.assertFalse((self.workspace / "SOUL.md").exists())
        self.assertFalse((self.config_path.parent / "agent-manage/agents/demo.json").exists())

    def test_restart_is_scoped_to_selected_environment_and_requires_rpc(self):
        result = self.manager.refresh_agent(self.request(restart=True))
        self.assertTrue(result["activation_verified"])
        self.assertFalse(result["restart_required"])
        calls = [(args, kwargs) for args, kwargs in self.runner.calls if "gateway" in args]
        self.assertEqual(calls[1][0], ["openclaw", "gateway", "restart"])
        self.assertEqual(calls[0][1]["env_overrides"]["OPENCLAW_CONFIG_PATH"], str(self.config_path))

    def test_restart_scope_mismatch_aborts_before_changing_files(self):
        self.runner.scope_mismatch = True
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "service uses this config"):
            self.manager.refresh_agent(self.request(restart=True))
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse((self.workspace / "SOUL.md").exists())
        self.assertFalse(any("restart" in args for args, kwargs in self.runner.calls))

    def test_custom_provider_collision_and_multiple_model_routes_are_rejected(self):
        self.config["models"]["providers"]["openai"] = {"baseUrl": "https://custom.example/v1", "apiKey": "custom", "models": []}
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(FileExistsError, "custom provider"):
            self.manager.refresh_agent(RefreshAgentRequest("demo", models_only=True))
        self.config["models"]["providers"]["openai"]["baseUrl"] = "https://api.dolaio.cn/aigateway/shop/v1"
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, "Multiple Dola"):
            self.manager.refresh_agent(RefreshAgentRequest("demo", models_only=True))

    def test_environment_lock_rejects_concurrent_refresh(self):
        state_root = self.config_path.parent / "agent-manage"
        state_root.mkdir()
        with (state_root / "refresh.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(FileExistsError, "Another refresh"):
                self.manager.refresh_agent(self.request())
        self.assertFalse((self.workspace / "SOUL.md").exists())

    @unittest.skipUnless(os.environ.get("AGENT_MANAGE_TEST_OPENCLAW_BIN"), "Set the server-target OpenClaw executable to run integration validation")
    def test_refreshed_config_validates_with_server_target_openclaw(self):
        binary = os.environ["AGENT_MANAGE_TEST_OPENCLAW_BIN"]
        version = subprocess.run([binary, "--version"], text=True, capture_output=True, timeout=30, check=True)
        self.assertIn("2026.7.1-2", version.stdout)
        config = {"agents": self.config["agents"], "models": self.config["models"]}
        for provider in config["models"]["providers"].values():
            provider["apiKey"] = "test-only-unused-key"
            provider.setdefault("api", "openai-completions")
            for model in provider["models"]:
                model["name"] = model["id"]
        self.config_path.write_text(json.dumps(config))
        runner = LocalRunner(openclaw_bin=binary)
        manager = InstanceManagerV2(runner, config_path=str(self.config_path))
        with patch.object(manager, "_fetch_supported_gateway_models", return_value=self.catalog), patch.object(runner, "log"):
            result = manager.refresh_agent(RefreshAgentRequest("demo", models_only=True))
        self.assertTrue(result["ok"])
        new_config = json.loads(self.config_path.read_text())
        self.assertEqual(new_config["agents"]["defaults"]["imageGenerationModel"], config["agents"]["defaults"]["imageGenerationModel"])

    def test_dry_run_conflicts_and_sources_without_persistent_writes(self):
        self.runner.dry_run = True
        (self.workspace / "SOUL.md").write_text("custom soul")
        result = self.manager.refresh_agent(self.request())
        self.assertTrue(result["skipped"])
        self.assertFalse(result["can_apply"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertFalse((self.config_path.parent / "agent-manage").exists())
        self.assertFalse(self.runner.calls)

    def test_zip_wrapper_permissions_and_traversal(self):
        archive_path = self.root / "package.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("wrapper/template.yaml", "version: 1.2.0\n")
            entry = zipfile.ZipInfo("wrapper/run.sh")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(entry, "#!/bin/sh\n")
        self.manager.refresh_agent(RefreshAgentRequest("demo", agent_zip=str(archive_path), template_only=True))
        self.assertTrue((self.workspace / "run.sh").stat().st_mode & stat.S_IXUSR)
        self.assertEqual(self.state()["version"], "1.2.0")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape", "bad")
        with self.assertRaises(ValueError):
            self.manager.refresh_agent(RefreshAgentRequest("demo", agent_zip=str(archive_path), template_only=True))
        self.assertFalse((self.root / "escape").exists())

    def test_shared_skills_are_scoped_and_user_extras_are_preserved(self):
        self.write_source("common-skills/weather/SKILL.md", "one")
        self.write_source("common-skills/weather/stale.txt", "stale")
        self.manager.refresh_agent(self.request())
        shared = self.config_path.parent / "skills/weather"
        (shared / "custom.txt").write_text("custom")
        self.write_source("common-skills/weather/SKILL.md", "two")
        (self.source / "common-skills/weather/stale.txt").unlink()
        result = self.manager.refresh_agent(self.request())
        self.assertEqual((shared / "SKILL.md").read_text(), "two")
        self.assertFalse((shared / "stale.txt").exists())
        self.assertEqual((shared / "custom.txt").read_text(), "custom")
        self.assertFalse((self.workspace / "common-skills").exists())
        self.assertEqual(result["template"]["common_skills_scope"], "environment")

    def test_unchanged_refresh_does_not_create_duplicate_backup(self):
        first = self.manager.refresh_agent(self.request())
        second = self.manager.refresh_agent(self.request())
        self.assertIsNone(second["backup_path"])
        self.assertFalse(second["restart_required"])
        self.assertEqual(len(list(Path(first["backup_path"]).parent.iterdir())), 1)

    def test_stale_template_cache_cannot_downgrade_installed_release(self):
        self.manager.refresh_agent(self.request())
        self.write_source("template.yaml", "version: 0.9.0\n")
        self.write_source("SOUL.md", "older soul")
        with self.assertRaisesRegex(ValueError, "Refusing template downgrade"):
            self.manager.refresh_agent(self.request(replace_modified=True))
        self.assertEqual(self.state()["version"], "1.0.0")
        self.assertEqual((self.workspace / "SOUL.md").read_text(), "version one\n")

    def test_source_and_target_symlinks_and_team_templates_are_rejected(self):
        target = self.workspace / "SOUL.md"
        target.symlink_to(self.root / "escape")
        with self.assertRaisesRegex(ValueError, "links"):
            self.manager.refresh_agent(self.request(replace_modified=True))
        target.unlink()
        self.write_source("template.yaml", "version: 1.0.0\ncopyMode: multi_agent_template\n")
        with self.assertRaisesRegex(ValueError, "single agent"):
            self.manager.refresh_agent(self.request())

    def test_required_dependency_never_runs_install_command(self):
        self.write_source("template.yaml", 'version: 1.0.0\nrequiredLibraries:\n  - name: missing\n    bin: definitely-nonexistent-dola-binary\n    installCommand: touch /tmp/never-run\n')
        with self.assertRaisesRegex(ValueError, "Required dependency"):
            self.manager.refresh_agent(self.request())
        self.assertFalse(self.runner.calls)

    def test_failed_health_also_restores_shared_skills(self):
        self.write_source("common-skills/weather/SKILL.md", "one")
        self.write_source("common-skills/weather/stale.txt", "stale")
        self.manager.refresh_agent(self.request())
        old_state = self.state()
        self.write_source("common-skills/weather/SKILL.md", "two")
        (self.source / "common-skills/weather/stale.txt").unlink()
        self.write_source("common-skills/weather/added.txt", "added")
        self.runner.fail_health_once = True
        with self.assertRaises(RuntimeError):
            self.manager.refresh_agent(self.request(restart=True))
        shared = self.config_path.parent / "skills/weather"
        self.assertEqual((shared / "SKILL.md").read_text(), "one")
        self.assertEqual((shared / "stale.txt").read_text(), "stale")
        self.assertFalse((shared / "added.txt").exists())
        self.assertEqual(self.state(), old_state)

    def test_managed_runtime_policy_survives_template_rules_update(self):
        self.write_source("AGENTS.md", "template rules\n")
        block = self.manager.RUNTIME_POLICY_START + "\nplatform policy\n" + self.manager.RUNTIME_POLICY_END
        (self.workspace / "AGENTS.md").write_text("template rules\n\n" + block + "\n")
        self.manager.refresh_agent(self.request())
        self.write_source("AGENTS.md", "new rules\n")
        self.manager.refresh_agent(self.request())
        self.assertEqual((self.workspace / "AGENTS.md").read_text(), "new rules\n\n" + block + "\n")

    def test_cli_wires_scope_and_template_request(self):
        output = io.StringIO()
        with patch.object(InstanceManagerV2, "refresh_agent", return_value={"ok": True}) as operation, redirect_stdout(output):
            code = main(["refresh-agent", "--agent", "demo", "--template-dir", str(self.source), "--template-only", "--restart"])
        self.assertEqual(code, 0)
        request = operation.call_args.args[0]
        self.assertTrue(request.template_only)
        self.assertTrue(request.restart)
        self.assertEqual(request.template_dir, str(self.source))
        self.assertEqual(json.loads(output.getvalue())["typeCode"], 1)


if __name__ == "__main__":
    unittest.main()
