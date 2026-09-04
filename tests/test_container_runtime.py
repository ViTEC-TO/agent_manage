import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_manage import container_runtime


class ContainerRuntimeTest(unittest.TestCase):
    def test_resolve_nginx_port_defaults_to_port_80(self):
        self.assertEqual(container_runtime.resolve_nginx_port(None), 80)
        self.assertEqual(container_runtime.resolve_nginx_port("8080"), 8080)

    def test_resolve_nginx_port_rejects_invalid_values(self):
        for value in ("nope", "0", "65536"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                container_runtime.resolve_nginx_port(value)

    def test_validate_nginx_config_rejects_proxy_and_workspace_escape(self):
        valid = """
pid /tmp/nginx/nginx.pid;
error_log /tmp/nginx/log/error.log warn;
http {
include /etc/nginx/mime.types;
access_log /tmp/nginx/log/access.log;
client_body_temp_path /tmp/nginx/client_temp;
proxy_temp_path /tmp/nginx/proxy_temp;
fastcgi_temp_path /tmp/nginx/fastcgi_temp;
uwsgi_temp_path /tmp/nginx/uwsgi_temp;
scgi_temp_path /tmp/nginx/scgi_temp;
server {
listen 8080;
root /home/node/.openclaw/workspace/public;
autoindex off;
disable_symlinks on;
}
}
"""
        container_runtime.validate_nginx_config(valid, 8080)
        with self.assertRaises(ValueError):
            container_runtime.validate_nginx_config(valid + "proxy_pass http://host;", 8080)
        with self.assertRaises(ValueError):
            container_runtime.validate_nginx_config(valid.replace("workspace/public", "workspace"), 8080)

    def test_initialize_preserves_existing_platform_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "state/nginx/nginx.conf"
            template_path = root / "image/nginx.conf"
            public_root = root / "state/workspace/public"
            runtime_root = root / "runtime"
            template_path.parent.mkdir(parents=True)
            runtime_config_path = runtime_root.as_posix()
            public_config_path = public_root.as_posix()
            template_path.write_text(
                f"pid {runtime_config_path}/nginx.pid;\n"
                f"error_log {runtime_config_path}/log/error.log warn;\n"
                "http {\n"
                "include /etc/nginx/mime.types;\n"
                f"access_log {runtime_config_path}/log/access.log;\n"
                f"client_body_temp_path {runtime_config_path}/client_temp;\n"
                f"proxy_temp_path {runtime_config_path}/proxy_temp;\n"
                f"fastcgi_temp_path {runtime_config_path}/fastcgi_temp;\n"
                f"uwsgi_temp_path {runtime_config_path}/uwsgi_temp;\n"
                f"scgi_temp_path {runtime_config_path}/scgi_temp;\n"
                "server {\n"
                "listen __UNITAG_NGINX_PORT__;\n"
                f"root {public_config_path};\n"
                "autoindex off;\n"
                "disable_symlinks on;\n"
                "}\n"
                "}\n",
                encoding="utf-8",
            )
            with (
                patch.object(container_runtime, "NGINX_CONFIG_PATH", config_path),
                patch.object(container_runtime, "NGINX_CONFIG_TEMPLATE", template_path),
                patch.object(container_runtime, "PUBLIC_ROOT", public_root),
                patch.object(container_runtime, "NGINX_RUNTIME_ROOT", runtime_root),
            ):
                container_runtime.initialize_nginx_config(8080)
                first = config_path.read_text(encoding="utf-8")
                container_runtime.initialize_nginx_config(8080)
                self.assertEqual(config_path.read_text(encoding="utf-8"), first)
                self.assertTrue(public_root.is_dir())


if __name__ == "__main__":
    unittest.main()
