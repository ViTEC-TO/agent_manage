import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_manage.skill_permissions import prepare_shared_skill_path


class SkillPermissionsTest(unittest.TestCase):
    def test_root_installs_for_environment_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("skill")
            environment = SimpleNamespace(stat=lambda: SimpleNamespace(st_uid=os.getuid() + 1, st_gid=os.getgid()))
            with patch("agent_manage.skill_permissions.os.geteuid", return_value=0), patch("agent_manage.skill_permissions.os.chown") as chown:
                prepare_shared_skill_path(skill, environment)
            self.assertEqual(chown.call_count, 2)
            chown.assert_any_call(skill, os.getuid() + 1, os.getgid(), follow_symlinks=False)

    def test_unprivileged_owner_mismatch_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp)
            environment = SimpleNamespace(stat=lambda: SimpleNamespace(st_uid=os.getuid() + 1, st_gid=os.getgid()))
            with patch("agent_manage.skill_permissions.os.geteuid", return_value=1000):
                with self.assertRaisesRegex(PermissionError, "ownership mismatch"):
                    prepare_shared_skill_path(skill, environment)

    def test_does_not_change_link_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = Path(tmp)
            outside = environment / "outside"
            outside.write_text("private")
            outside.chmod(0o400)
            skill = environment / "skill"
            skill.mkdir()
            (skill / "link").symlink_to(outside)
            prepare_shared_skill_path(skill, environment)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o400)
            with self.assertRaisesRegex(ValueError, "must not be a link"):
                prepare_shared_skill_path(skill / "link", environment)
