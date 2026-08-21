import os
import unittest
from unittest.mock import patch

from agent_manage.local import LocalRunner


class LocalRunnerEnvironmentTest(unittest.TestCase):
    def test_command_path_contains_npm_and_system_sbin_directories(self):
        runner = LocalRunner()

        with patch.dict(
            os.environ,
            {
                "HOME": "/tmp/agent-manage-home",
                "PATH": "/usr/bin:/bin",
            },
            clear=True,
        ):
            env = runner._command_env()

        path_parts = env["PATH"].split(os.pathsep)
        self.assertEqual(path_parts[0], "/tmp/agent-manage-home/.npm-global/bin")
        self.assertIn("/usr/local/sbin", path_parts)
        self.assertIn("/usr/sbin", path_parts)
        self.assertIn("/sbin", path_parts)
        self.assertEqual(len(path_parts), len(set(path_parts)))


if __name__ == "__main__":
    unittest.main()
