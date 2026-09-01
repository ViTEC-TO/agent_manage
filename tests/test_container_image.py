import tempfile
import unittest
import json
from pathlib import Path

from agent_manage.seed import (
    INITIALIZING_MARKER,
    assert_seed_has_no_runtime_secrets,
    initialize_openclaw_seed,
)


class ContainerImageTest(unittest.TestCase):
    def test_nonempty_runtime_config_is_merged_over_prebuilt_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            target = root / "target"
            (seed / "data" / "base").mkdir(parents=True)
            (seed / "openclaw.json").write_text(
                json.dumps(
                    {
                        "agents": {"list": [{"id": "unipay-claw-base"}]},
                        "gateway": {"auth": {"mode": "token"}},
                        "seedOnly": {"enabled": True},
                    }
                ),
                encoding="utf-8",
            )
            (seed / "data" / "base" / "AGENTS.md").write_text("seed\n", encoding="utf-8")
            target.mkdir()
            (target / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {"auth": {"token": "runtime-gateway-token"}},
                        "runtimeOnly": {"value": 1},
                    }
                ),
                encoding="utf-8",
            )

            result = initialize_openclaw_seed(seed, target)
            merged = json.loads((target / "openclaw.json").read_text(encoding="utf-8"))

            self.assertTrue(result["initialized"])
            self.assertEqual(merged["agents"]["list"][0]["id"], "unipay-claw-base")
            self.assertEqual(merged["gateway"]["auth"]["token"], "runtime-gateway-token")
            self.assertEqual(merged["gateway"]["auth"]["mode"], "token")
            self.assertEqual(merged["runtimeOnly"], {"value": 1})
            self.assertTrue((target / "data" / "base" / "AGENTS.md").is_file())
            self.assertNotIn("runtime-gateway-token", (seed / "openclaw.json").read_text(encoding="utf-8"))

    def test_initialized_nonempty_target_is_not_reinitialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            target = root / "target"
            seed.mkdir()
            target.mkdir()
            (seed / "openclaw.json").write_text('{"agents": {}}\n', encoding="utf-8")
            (target / "openclaw.json").write_text(
                '{"gateway": {"auth": {"token": "runtime-gateway-token"}}}\n',
                encoding="utf-8",
            )

            initialize_openclaw_seed(seed, target)
            (target / "runtime.db").write_text("runtime\n", encoding="utf-8")
            second = initialize_openclaw_seed(seed, target)

            self.assertFalse(second["initialized"])
            self.assertEqual(second["reason"], "already_initialized")
            self.assertEqual((target / "runtime.db").read_text(encoding="utf-8"), "runtime\n")

    def test_seed_initialization_is_idempotent_and_preserves_runtime_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            target = root / "target"
            (seed / "data" / "base").mkdir(parents=True)
            (seed / "openclaw.json").write_text('{"agents": {}}\n', encoding="utf-8")
            (seed / "data" / "base" / "AGENTS.md").write_text("seed\n", encoding="utf-8")

            first = initialize_openclaw_seed(seed, target)
            (target / "runtime.db").write_text("runtime\n", encoding="utf-8")
            second = initialize_openclaw_seed(seed, target)

            self.assertTrue(first["initialized"])
            self.assertFalse(second["initialized"])
            self.assertEqual((target / "runtime.db").read_text(encoding="utf-8"), "runtime\n")

    def test_seed_initialization_resumes_an_interrupted_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            target = root / "target"
            seed.mkdir()
            target.mkdir()
            (seed / "openclaw.json").write_text("{}\n", encoding="utf-8")
            (target / INITIALIZING_MARKER).touch()
            (target / "partial").write_text("old\n", encoding="utf-8")
            (target / "openclaw.json").write_text(
                '{"gateway": {"auth": {"token": "runtime-gateway-token"}}}\n',
                encoding="utf-8",
            )

            result = initialize_openclaw_seed(seed, target)

            self.assertTrue(result["initialized"])
            self.assertTrue((target / "openclaw.json").is_file())
            self.assertFalse((target / INITIALIZING_MARKER).exists())
            self.assertEqual(
                json.loads((target / "openclaw.json").read_text(encoding="utf-8"))["gateway"]["auth"]["token"],
                "runtime-gateway-token",
            )

    def test_missing_seed_fails_stably(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(FileNotFoundError, "OpenClaw seed is missing or empty"):
                initialize_openclaw_seed(root / "missing", root / "target")

    def test_image_build_assets_do_not_accept_runtime_secrets(self):
        dockerfile = (
            Path(__file__).resolve().parents[1] / "container-image" / "Dockerfile"
        ).read_text(encoding="utf-8")
        upper = dockerfile.upper()
        self.assertNotIn("MODEL_KEY", upper)
        self.assertNotIn("GATEWAY_TOKEN", upper)
        self.assertNotIn("AUTH_TOKEN", upper)
        self.assertIn("/opt/unitag/openclaw-seed", dockerfile)
        self.assertIn("io.dola.unitag.template-identify", dockerfile)

    def test_seed_validation_rejects_config_and_profile_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp)
            (seed / "openclaw.json").write_text(
                '{"gateway": {"auth": {"token": "must-not-ship"}}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "gateway.auth.token"):
                assert_seed_has_no_runtime_secrets(seed)

            (seed / "openclaw.json").write_text("{}\n", encoding="utf-8")
            (seed / "auth-profiles.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Secret-bearing file"):
                assert_seed_has_no_runtime_secrets(seed)


if __name__ == "__main__":
    unittest.main()
