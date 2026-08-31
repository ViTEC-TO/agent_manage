"""Template extraction, agent provisioning, skills, and workspace policies."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .local import CommandError
from .models import AddAgentRequest, CreateInstanceRequest
from .settings import normalize_image_quality
from .template_safety import (
    require_path_within,
    validate_safe_name,
    validate_template_archive,
)


class ProvisioningMixin:
    """Provision agents from templates while preserving existing workspaces."""

    def resolve_agent_name(self, request: CreateInstanceRequest) -> str:
        template_name = (request.template_name or "").strip()
        if template_name:
            return validate_safe_name(template_name, "template_name")
        if request.agent_zip:
            return validate_safe_name(
                Path(request.agent_zip).expanduser().resolve().stem,
                "agent_name",
            )
        raise ValueError("template_name is required")

    def default_workspace(self, agent_name: str, workspace_root: str) -> Path:
        safe_agent_name = validate_safe_name(agent_name, "agent_name")
        return Path(workspace_root).expanduser().resolve() / safe_agent_name

    def resolve_add_agent_template_name(self, request: AddAgentRequest) -> str:
        return validate_safe_name(
            request.template_name or request.agent_name,
            "template_name",
        )

    def resolve_add_agent_workspace(self, request: AddAgentRequest, workspace_root: str) -> Path:
        validate_safe_name(request.agent_name, "agent_name")
        if request.workspace:
            return Path(request.workspace).expanduser().resolve()
        return self.default_workspace(request.agent_name, workspace_root)

    def resolve_archive_path(self, request: CreateInstanceRequest) -> Path:
        if request.agent_zip:
            return Path(request.agent_zip).expanduser().resolve()
        return self._template_root_for_request(request) / f"{self.resolve_agent_name(request)}.zip"

    def resolve_template_dir(self, request: CreateInstanceRequest) -> Path:
        return self._template_root_for_request(request) / self.resolve_agent_name(request)

    def _template_root_for_request(self, request: CreateInstanceRequest) -> Path:
        if request.local and not self.template_root_explicit:
            return Path(self.LOCAL_TEMPLATE_ROOT).expanduser().resolve()
        return self.template_root

    def _ensure_sources_ready(self, archive_path: Path) -> None:
        if not archive_path.is_file():
            raise FileNotFoundError(f"Template archive not found: {archive_path}")

    def _workspace_has_content(self, workspace: Path) -> bool:
        if not workspace.exists():
            return False
        if not workspace.is_dir():
            raise NotADirectoryError(f"Workspace path is not a directory: {workspace}")
        return any(workspace.iterdir())

    def _agent_exists(self, agent_name: str) -> bool:
        if self.runner.dry_run:
            return False
        config = self._load_config()
        for item in self._extract_agent_list(config):
            if item.get("id") == agent_name:
                return True
        return False

    def _ensure_agent_exists(self, agent_name: str) -> None:
        if self.runner.dry_run or self._agent_exists(agent_name):
            return
        raise FileNotFoundError(f"Agent not found: {agent_name}")

    def _ensure_agent_exists_in_config(self, agent_name: str) -> None:
        if self.runner.dry_run:
            return
        config = self._load_config()
        for item in self._extract_agent_list(config):
            if item.get("id") == agent_name:
                return
        raise FileNotFoundError(f"Agent not found in config: {agent_name}")

    def _add_agent(self, agent_name: str, workspace: Path, model: Optional[str]) -> Dict[str, object]:
        args = [
            self.bin,
            "agents",
            "add",
            agent_name,
            "--workspace",
            str(workspace),
            "--non-interactive",
            "--json",
        ]
        if model:
            args.extend(["--model", model])
        result = self.runner.run(
            args,
            timeout=self.OPENCLAW_COMMAND_TIMEOUT_SECONDS,
        )
        payload = self.runner._extract_json(result.stdout) if result.stdout.strip() else {}
        payload["command"] = result.command_text
        payload["returncode"] = result.returncode
        if result.skipped:
            payload["skipped"] = True
        return payload

    def _prepare_template_dir(self, archive_path: Path, template_dir: Path) -> Dict[str, object]:
        validate_template_archive(archive_path)
        if self.runner.dry_run:
            return {
                "skipped": True,
                "archive_path": str(archive_path),
                "template_dir": str(template_dir),
            }

        if template_dir.exists() and not template_dir.is_dir():
            raise NotADirectoryError(f"Template path is not a directory: {template_dir}")

        with tempfile.TemporaryDirectory() as tmpdir:
            extract_dir = Path(tmpdir)
            shutil.unpack_archive(str(archive_path), str(extract_dir))
            source_root = self._resolve_unpacked_root(extract_dir)
            template_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{template_dir.name}.staging-",
                    dir=str(template_dir.parent),
                )
            )
            backup_dir: Optional[Path] = None
            try:
                copied = self._copy_directory_contents(source_root, staging_dir)
                if template_dir.exists():
                    backup_dir = Path(
                        tempfile.mkdtemp(
                            prefix=f".{template_dir.name}.backup-",
                            dir=str(template_dir.parent),
                        )
                    )
                    backup_dir.rmdir()
                    template_dir.replace(backup_dir)
                try:
                    staging_dir.replace(template_dir)
                except Exception:
                    if backup_dir is not None and backup_dir.exists():
                        backup_dir.replace(template_dir)
                    raise
                if backup_dir is not None:
                    shutil.rmtree(backup_dir)
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
        return {
            "template_dir": str(template_dir),
            "archive_path": str(archive_path),
            "copied_into_template_dir": copied,
        }

    def _populate_workspace(self, template_dir: Path, workspace: Path) -> Dict[str, object]:
        if self.runner.dry_run:
            return {
                "skipped": True,
                "template_dir": str(template_dir),
                "workspace": str(workspace),
            }
        if workspace.exists() and not workspace.is_dir():
            raise NotADirectoryError(f"Workspace path is not a directory: {workspace}")
        workspace.mkdir(parents=True, exist_ok=True)
        copied = self._copy_directory_contents(template_dir, workspace)

        return {
            "template_dir": str(template_dir),
            "workspace": str(workspace),
            "copied_from_template_dir": copied,
        }

    def _load_template_manifest(self, template_dir: Path) -> Dict[str, object]:
        manifest_path = template_dir / "template.yaml"
        if not manifest_path.is_file():
            return {}
        text = manifest_path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore
        except ImportError:
            return self._parse_template_manifest_yaml_subset(text)

        payload = yaml.safe_load(text)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError(f"Template manifest must be a YAML object: {manifest_path}")
        return payload

    def _parse_template_manifest_yaml_subset(self, text: str) -> Dict[str, object]:
        manifest: Dict[str, object] = {}
        current_key: Optional[str] = None
        current_item: Optional[Dict[str, object]] = None
        list_keys = {"agents", "commonSkillFolders", "requiredLibraries"}

        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            if indent == 0:
                current_item = None
                if ":" not in stripped:
                    current_key = None
                    continue
                key, value = stripped.split(":", 1)
                key = key.strip()
                value = value.strip()
                if key in list_keys and value == "":
                    manifest[key] = []
                    current_key = key
                else:
                    manifest[key] = self._parse_simple_yaml_value(value)
                    current_key = None
                continue
            if current_key not in list_keys:
                continue
            if indent == 2 and stripped.startswith("- "):
                manifest.setdefault(current_key, [])
                items = manifest[current_key]
                inline = stripped[2:].strip()
                if inline and ":" in inline:
                    current_item = {}
                    if isinstance(items, list):
                        items.append(current_item)
                    key, value = inline.split(":", 1)
                    current_item[key.strip()] = self._parse_simple_yaml_value(value.strip())
                elif inline:
                    current_item = None
                    if isinstance(items, list):
                        items.append(self._parse_simple_yaml_value(inline))
                else:
                    current_item = {}
                    if isinstance(items, list):
                        items.append(current_item)
                continue
            if current_item is not None and indent >= 4 and ":" in stripped:
                key, value = stripped.split(":", 1)
                current_item[key.strip()] = self._parse_simple_yaml_value(value.strip())
        return manifest

    def _parse_simple_yaml_value(self, value: str):
        if value == "":
            return ""
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "none"}:
            return None
        if value.startswith('"') and value.endswith('"'):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value[1:-1]
        if value.startswith("'") and value.endswith("'"):
            return value[1:-1]
        return value

    def _required_libraries_from_manifest(
        self,
        manifest: Dict[str, object],
    ) -> List[Dict[str, object]]:
        libraries = manifest.get("requiredLibraries")
        if not isinstance(libraries, list):
            return []
        return [item for item in libraries if isinstance(item, dict)]

    def _common_skill_sources_from_manifest(
        self,
        template_dir: Path,
        manifest: Dict[str, object],
    ) -> List[Path]:
        sources: List[Path] = []
        seen = set()

        def add_source(path_value: object) -> None:
            if not isinstance(path_value, str) or not path_value.strip():
                return
            source = require_path_within(
                template_dir / path_value,
                template_dir,
                "common skill path",
            )
            if source in seen:
                return
            seen.add(source)
            sources.append(source)

        configured = manifest.get("commonSkillFolders")
        if isinstance(configured, list):
            for item in configured:
                if isinstance(item, dict):
                    add_source(item.get("path"))
                else:
                    add_source(item)

        common_skills_dir = template_dir / "common-skills"
        if common_skills_dir.is_dir():
            for item in sorted(common_skills_dir.iterdir(), key=lambda entry: entry.name):
                if item.is_dir():
                    add_source(str(item.relative_to(template_dir)))
        return sources

    def _builtin_common_skill_sources(self) -> List[Path]:
        root = Path(__file__).resolve().parent / "common_skills"
        if not root.is_dir():
            raise FileNotFoundError(f"Built-in common skills folder not found: {root}")
        return sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name)

    def _multi_agent_specs_from_template(
        self,
        *,
        template_dir: Path,
        manifest: Dict[str, object],
        primary_agent_name: str,
        workspace_root: str,
        fallback_model: Optional[str],
    ) -> List[Dict[str, object]]:
        raw_agents = manifest.get("agents")
        is_multi_agent = manifest.get("copyMode") == "multi_agent_template" or isinstance(raw_agents, list)
        agents_dir = template_dir / "agents"
        if not is_multi_agent:
            return []

        specs: List[Dict[str, object]] = []
        seen = {primary_agent_name}

        def add_spec(name_value: object, source_value: object = None, workspace_value: object = None, model_value: object = None) -> None:
            source = str(source_value or "").strip()
            if not source:
                if not isinstance(name_value, str) or not name_value.strip():
                    return
                source = f"agents/{name_value.strip()}"
            source_dir = require_path_within(
                template_dir / source,
                template_dir,
                "multi-agent source",
            )
            if source in {".", "./"} or source_dir == template_dir:
                return

            name = validate_safe_name(
                str(name_value or source_dir.name),
                "agent_name",
            )
            if name in seen:
                return
            seen.add(name)

            specs.append(
                {
                    "agent_name": name,
                    "template_name": name,
                    "source": source,
                    "template_dir": source_dir,
                    "workspace": self._resolve_multi_agent_workspace(
                        workspace_value=workspace_value,
                        agent_name=name,
                        workspace_root=workspace_root,
                    ),
                    "model": model_value if isinstance(model_value, str) and model_value.strip() else fallback_model,
                }
            )

        if isinstance(raw_agents, list):
            for item in raw_agents:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("agent_name")
                add_spec(
                    name_value=name,
                    source_value=item.get("source"),
                    workspace_value=item.get("workspace"),
                    model_value=item.get("model"),
                )

        if agents_dir.is_dir():
            for item in sorted(agents_dir.iterdir(), key=lambda entry: entry.name):
                if item.is_dir():
                    add_spec(
                        name_value=item.name,
                        source_value=str(item.relative_to(template_dir)),
                        workspace_value=item.name,
                    )

        return specs

    def _resolve_multi_agent_workspace(
        self,
        *,
        workspace_value: object,
        agent_name: str,
        workspace_root: str,
    ) -> Path:
        workspace_name = str(workspace_value or agent_name).strip() or agent_name
        workspace_path = Path(workspace_name).expanduser()
        if workspace_path.is_absolute():
            raise ValueError("Template agent workspace must be relative to workspace_root")
        resolved_root = Path(workspace_root).expanduser().resolve()
        resolved_workspace = (resolved_root / workspace_path).resolve()
        if not resolved_workspace.is_relative_to(resolved_root):
            raise ValueError(f"Template agent workspace escapes workspace_root: {workspace_name}")
        return resolved_workspace

    def _ensure_required_libraries(
        self,
        libraries: List[Dict[str, object]],
    ) -> Dict[str, object]:
        results = []
        for library in libraries:
            name = str(library.get("name") or library.get("bin") or "").strip()
            if not name:
                raise ValueError(f"requiredLibraries item missing name: {library}")
            required = library.get("required") is not False
            installed, check_result = self._library_is_installed(library)
            item_result: Dict[str, object] = {
                "name": name,
                "required": required,
                "installed_before": installed,
                "check": check_result,
            }
            if installed:
                item_result["action"] = "continue"
                results.append(item_result)
                continue

            install_command = str(library.get("installCommand") or "").strip()
            if not install_command:
                if required:
                    raise RuntimeError(f"Required library '{name}' is not installed and has no installCommand")
                item_result["action"] = "skipped"
                item_result["reason"] = "not_required_without_install_command"
                results.append(item_result)
                continue

            install_result = self.runner.run(
                ["/bin/sh", "-lc", install_command],
                timeout=self.LIBRARY_INSTALL_TIMEOUT_SECONDS,
            )
            item_result["action"] = "installed"
            item_result["install"] = self._command_result_payload(install_result)
            installed_after, verify_after = self._library_is_installed(library)
            item_result["installed_after"] = installed_after
            item_result["verify_after"] = verify_after
            if not installed_after:
                raise RuntimeError(f"Required library '{name}' install completed but verification still failed")
            results.append(item_result)

        return {
            "library_count": len(libraries),
            "libraries": results,
        }

    def _library_is_installed(self, library: Dict[str, object]) -> tuple[bool, Dict[str, object]]:
        verify_command = str(library.get("verifyCommand") or "").strip()
        bin_name = str(library.get("bin") or "").strip()
        if verify_command:
            command = verify_command
        elif bin_name:
            command = f"command -v {bin_name}"
        else:
            return False, {"skipped": True, "reason": "missing_verify_command_or_bin"}

        try:
            result = self.runner.run(
                ["/bin/sh", "-lc", command],
                timeout=self.LIBRARY_VERIFY_TIMEOUT_SECONDS,
            )
        except CommandError as exc:
            return False, {
                "command": exc.result.command_text,
                "returncode": exc.result.returncode,
                "stdout": exc.result.stdout,
                "stderr": exc.result.stderr,
            }
        return True, self._command_result_payload(result)

    def _install_common_skills(self, sources: List[Path]) -> Dict[str, object]:
        target_root = self.config_path.parent / "skills"
        if self.runner.dry_run:
            return {
                "skipped": True,
                "target_root": str(target_root),
                "skill_count": len(sources),
                "skills": [source.name for source in sources],
            }
        target_root.mkdir(parents=True, exist_ok=True)
        installed = []
        for source in sources:
            if not source.is_dir():
                raise FileNotFoundError(f"Common skill folder not found: {source}")
            destination = target_root / source.name
            shutil.copytree(source, destination, dirs_exist_ok=True)
            installed.append(
                {
                    "name": source.name,
                    "source": str(source),
                    "destination": str(destination),
                }
            )
        return {
            "target_root": str(target_root),
            "skill_count": len(installed),
            "skills": installed,
        }

    def _configure_config_tools(self, agent_names: Optional[List[str]] = None) -> Dict[str, object]:
        config_path = self.config_path
        normalized_agent_names = self._normalize_agent_to_agent_allow(agent_names or [])
        if self.runner.dry_run:
            return {
                "skipped": True,
                "config_path": str(config_path),
                "tools_profile": "coding",
                "exec_security": "full",
                "web_search_enabled": False,
                "web_fetch_enabled": True,
                "agent_to_agent_enabled": True,
                "agent_to_agent_allow": normalized_agent_names,
                "sessions_visibility": "all",
            }

        config = self._load_config()
        if not isinstance(config, dict):
            raise ValueError(f"Config must be a JSON object: {config_path}")

        tools = config.setdefault("tools", {})
        tools["profile"] = "coding"
        exec_config = tools.setdefault("exec", {})
        exec_config["security"] = "full"
        web = tools.setdefault("web", {})
        web["search"] = {"enabled": False}
        web["fetch"] = {"enabled": True}
        agent_to_agent = tools.setdefault("agentToAgent", {})
        agent_to_agent["enabled"] = True
        agent_to_agent["allow"] = self._merge_agent_to_agent_allow(
            agent_to_agent.get("allow"),
            normalized_agent_names,
        )
        sessions = tools.setdefault("sessions", {})
        sessions["visibility"] = "all"

        self._write_config(
            config,
            note="configure tools",
            changed_paths=[
                "tools.profile",
                "tools.exec.security",
                "tools.web.search",
                "tools.web.fetch",
                "tools.agentToAgent",
                "tools.sessions.visibility",
            ],
            extra={
                "tools_profile": "coding",
                "exec_security": "full",
                "web_search_enabled": False,
                "web_fetch_enabled": True,
                "agent_to_agent_enabled": True,
                "agent_to_agent_allow": agent_to_agent["allow"],
                "sessions_visibility": "all",
            },
        )
        return {
            "config_path": str(config_path),
            "tools_profile": "coding",
            "exec_security": "full",
            "web_search_enabled": False,
            "web_fetch_enabled": True,
            "agent_to_agent_enabled": True,
            "agent_to_agent_allow": agent_to_agent["allow"],
            "sessions_visibility": "all",
        }

    def _configure_workspace_defaults(
        self,
        workspaces: List[Path],
        *,
        quality: str,
    ) -> Dict[str, object]:
        skills_result = self._install_common_skills(self._builtin_common_skill_sources())
        policy_result = self._configure_runtime_policy(workspaces, quality=quality)
        return {
            **policy_result,
            "common_skills": skills_result,
        }

    def _configure_runtime_policy(
        self,
        workspaces: List[Path],
        *,
        quality: str,
    ) -> Dict[str, object]:
        resolved_quality = normalize_image_quality(quality)

        policy_block = "\n".join(
            [
                self.RUNTIME_POLICY_START,
                "## Runtime rules",
                "",
                "- Preserve existing and unrelated changes; make only necessary changes and avoid destructive or system-wide actions unless explicitly authorized.",
                "- Run blocking commands separately with timeouts; bound network retries, prefer IPv4, and diagnose or change approach after two failures for the same reason.",
                "- Never claim unperformed verification; report validation gaps and risks, and inspect final diffs for temporary files, debug code, secrets, or unintended changes.",
                "- Ensure `PATH` includes `/usr/local/sbin:/usr/sbin:/sbin`; verify software with package/service state or absolute paths, not only `command -v`.",
                "- Do not run long-lived services in the foreground; verify their process, port, and key logs separately after startup.",
                "- All delivered files and `MEDIA:` attachments must use real public URLs reachable over IPv4, never local paths or IPv6 addresses; upload first when needed.",
                "- Public file, web, and static deliverables must follow the `nginx-delivery` Skill; deployment, index update, and a verified public URL are required for completion.",
                "- When calling `image_generate`, use "
                f"`quality: \"{resolved_quality}\"` unless the user explicitly requests another quality.",
                self.RUNTIME_POLICY_END,
            ]
        )
        configured: List[str] = []
        for workspace in workspaces:
            policy_path = workspace / "AGENTS.md"
            if self.runner.dry_run:
                configured.append(str(policy_path))
                continue
            if not workspace.is_dir():
                raise FileNotFoundError(f"Workspace not found: {workspace}")
            existing = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
            legacy_start = existing.find(self.IMAGE_GENERATION_POLICY_START)
            legacy_end = existing.find(self.IMAGE_GENERATION_POLICY_END)
            if (legacy_start == -1) != (legacy_end == -1):
                raise ValueError(f"Incomplete legacy image policy block: {policy_path}")
            if legacy_start != -1:
                legacy_end += len(self.IMAGE_GENERATION_POLICY_END)
                existing = existing[:legacy_start] + existing[legacy_end:]

            start = existing.find(self.RUNTIME_POLICY_START)
            end = existing.find(self.RUNTIME_POLICY_END)
            if (start == -1) != (end == -1):
                raise ValueError(f"Incomplete managed runtime policy block: {policy_path}")
            if start != -1:
                end += len(self.RUNTIME_POLICY_END)
                updated = existing[:start] + policy_block + existing[end:]
            else:
                prefix = existing.rstrip()
                updated = f"{prefix}\n\n{policy_block}\n" if prefix else f"{policy_block}\n"
            policy_path.write_text(updated, encoding="utf-8")
            configured.append(str(policy_path))
        return {
            "quality": resolved_quality,
            "policy_files": configured,
        }

    def _normalize_agent_to_agent_allow(self, agent_names: List[str]) -> List[str]:
        return self._dedupe_preserve_order(["main", *[name.strip() for name in agent_names if name.strip()]])

    def _merge_agent_to_agent_allow(self, current_allow: object, agent_names: List[str]) -> List[str]:
        existing = current_allow if isinstance(current_allow, list) else []
        merged = [str(item).strip() for item in existing if str(item).strip()]
        merged.extend(agent_names)
        return self._dedupe_preserve_order(merged)

    def _provision_agent_from_template(
        self,
        *,
        steps: List[Dict[str, object]],
        template_name: str,
        agent_name: str,
        archive_path: Path,
        template_dir: Path,
        workspace: Path,
        model: Optional[str],
        rollback_on_fail: bool,
        step_scope: Optional[str],
    ) -> Dict[str, object]:
        self._ensure_sources_ready(archive_path=archive_path)
        workspace_existed_before = workspace.exists()
        template_dir_existed_before = template_dir.exists()
        workspace_has_content = self._workspace_has_content(workspace)
        agent_exists = self._agent_exists(agent_name)

        created_agent = False
        started_template_prepare = False
        created_workspace = False

        try:
            started_template_prepare = True
            self._run_timed_step(
                steps,
                self._scoped_step_name("template.prepare", step_scope),
                lambda: self._prepare_template_dir(
                    archive_path=archive_path,
                    template_dir=template_dir,
                ),
            )
            manifest = self._load_template_manifest(template_dir)
            required_libraries = self._required_libraries_from_manifest(manifest)
            if required_libraries:
                self._run_timed_step(
                    steps,
                    self._scoped_step_name("libraries.ensure", step_scope),
                    lambda: self._ensure_required_libraries(required_libraries),
                )

            common_skill_sources = self._common_skill_sources_from_manifest(
                template_dir=template_dir,
                manifest=manifest,
            )
            if common_skill_sources:
                self._run_timed_step(
                    steps,
                    self._scoped_step_name("common_skills.install", step_scope),
                    lambda: self._install_common_skills(common_skill_sources),
                )

            if agent_exists:
                self.runner.log(f"agent exists, skip add: {agent_name}")
                agent_result = {
                    "skipped": True,
                    "reason": "agent_exists",
                    "agent_name": agent_name,
                }
                steps.append(
                    self._build_step_payload(
                        self._scoped_step_name("agents.add", step_scope),
                        agent_result,
                    )
                )
            else:
                self._run_timed_step(
                    steps,
                    self._scoped_step_name("agents.add", step_scope),
                    lambda: self._add_agent(
                        agent_name=agent_name,
                        workspace=workspace,
                        model=model,
                    ),
                )
                created_agent = True

            if workspace_has_content:
                self.runner.log(f"workspace not empty, skip populate: {workspace}")
                workspace_result = {
                    "skipped": True,
                    "reason": "workspace_not_empty",
                    "workspace": str(workspace),
                }
                steps.append(
                    self._build_step_payload(
                        self._scoped_step_name("workspace.populate", step_scope),
                        workspace_result,
                    )
                )
            else:
                workspace_result = self._run_timed_step(
                    steps,
                    self._scoped_step_name("workspace.populate", step_scope),
                    lambda: self._populate_workspace(
                        template_dir=template_dir,
                        workspace=workspace,
                    ),
                )
                created_workspace = (
                    not workspace_existed_before
                    and workspace.exists()
                    and not workspace_result.get("skipped", False)
                )
            return {
                "created_agent": created_agent,
                "started_template_prepare": started_template_prepare,
                "created_template_dir": (
                    not template_dir_existed_before and template_dir.exists()
                ),
                "created_workspace": created_workspace,
            }
        except Exception as exc:
            rollback_steps: List[Dict[str, object]] = []
            if rollback_on_fail:
                if not workspace_existed_before and workspace.exists():
                    self._run_timed_rollback_step(
                        rollback_steps, lambda: self._safe_purge_workspace(workspace)
                    )
                if created_agent:
                    self._run_timed_rollback_step(
                        rollback_steps, lambda: self._safe_delete_agent(agent_name)
                    )
                if not template_dir_existed_before and template_dir.exists():
                    self._run_timed_rollback_step(
                        rollback_steps, lambda: self._safe_purge_template_dir(template_dir)
                    )
            raise RuntimeError(
                json.dumps(
                    {
                        "error": str(exc),
                        "details": self._error_details(exc),
                        "context": {
                            "template_name": template_name,
                            "agent_name": agent_name,
                            "workspace": str(workspace),
                            "archive_path": str(archive_path),
                            "template_dir": str(template_dir),
                        },
                        "steps": steps,
                        "rollback": rollback_steps,
                    },
                    ensure_ascii=False,
                )
            ) from exc

    def _provision_agent_from_prepared_template(
        self,
        *,
        steps: List[Dict[str, object]],
        template_name: str,
        agent_name: str,
        template_dir: Path,
        workspace: Path,
        model: Optional[str],
        rollback_on_fail: bool,
        step_scope: Optional[str],
    ) -> Dict[str, object]:
        if not template_dir.is_dir():
            raise FileNotFoundError(f"Agent template folder not found: {template_dir}")
        workspace_existed_before = workspace.exists()
        workspace_has_content = self._workspace_has_content(workspace)
        agent_exists = self._agent_exists(agent_name)

        created_agent = False
        created_workspace = False

        try:
            manifest = self._load_template_manifest(template_dir)
            required_libraries = self._required_libraries_from_manifest(manifest)
            if required_libraries:
                self._run_timed_step(
                    steps,
                    self._scoped_step_name("libraries.ensure", step_scope),
                    lambda: self._ensure_required_libraries(required_libraries),
                )

            common_skill_sources = self._common_skill_sources_from_manifest(
                template_dir=template_dir,
                manifest=manifest,
            )
            if common_skill_sources:
                self._run_timed_step(
                    steps,
                    self._scoped_step_name("common_skills.install", step_scope),
                    lambda: self._install_common_skills(common_skill_sources),
                )

            if agent_exists:
                self.runner.log(f"agent exists, skip add: {agent_name}")
                agent_result = {
                    "skipped": True,
                    "reason": "agent_exists",
                    "agent_name": agent_name,
                }
                steps.append(
                    self._build_step_payload(
                        self._scoped_step_name("agents.add", step_scope),
                        agent_result,
                    )
                )
            else:
                self._run_timed_step(
                    steps,
                    self._scoped_step_name("agents.add", step_scope),
                    lambda: self._add_agent(
                        agent_name=agent_name,
                        workspace=workspace,
                        model=model,
                    ),
                )
                created_agent = True

            if workspace_has_content:
                self.runner.log(f"workspace not empty, skip populate: {workspace}")
                workspace_result = {
                    "skipped": True,
                    "reason": "workspace_not_empty",
                    "workspace": str(workspace),
                }
                steps.append(
                    self._build_step_payload(
                        self._scoped_step_name("workspace.populate", step_scope),
                        workspace_result,
                    )
                )
            else:
                workspace_result = self._run_timed_step(
                    steps,
                    self._scoped_step_name("workspace.populate", step_scope),
                    lambda: self._populate_workspace(
                        template_dir=template_dir,
                        workspace=workspace,
                    ),
                )
                created_workspace = (
                    not workspace_existed_before
                    and workspace.exists()
                    and not workspace_result.get("skipped", False)
                )

            return {
                "created_agent": created_agent,
                "started_template_prepare": False,
                "created_workspace": created_workspace,
            }
        except Exception as exc:
            rollback_steps: List[Dict[str, object]] = []
            if rollback_on_fail:
                if not workspace_existed_before and workspace.exists():
                    self._run_timed_rollback_step(
                        rollback_steps, lambda: self._safe_purge_workspace(workspace)
                    )
                if created_agent:
                    self._run_timed_rollback_step(
                        rollback_steps, lambda: self._safe_delete_agent(agent_name)
                    )
            raise RuntimeError(
                json.dumps(
                    {
                        "error": str(exc),
                        "details": self._error_details(exc),
                        "context": {
                            "template_name": template_name,
                            "agent_name": agent_name,
                            "workspace": str(workspace),
                            "template_dir": str(template_dir),
                        },
                        "steps": steps,
                        "rollback": rollback_steps,
                    },
                    ensure_ascii=False,
                )
            ) from exc

    def _resolve_unpacked_root(self, extract_dir: Path) -> Path:
        candidates = [item for item in extract_dir.iterdir() if item.name != "__MACOSX"]
        if not candidates:
            raise ValueError("Template archive is empty")
        directories = [item for item in candidates if item.is_dir()]
        files = [item for item in candidates if item.is_file()]
        if len(directories) == 1 and not files:
            return directories[0]
        return extract_dir

    def _copy_directory_contents(self, source_dir: Path, target_dir: Path) -> List[str]:
        copied: List[str] = []
        for item in sorted(source_dir.iterdir(), key=lambda entry: entry.name):
            destination = target_dir / item.name
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)
            copied.append(item.name)
        return copied

    def _safe_delete_agent(self, agent_name: str) -> Dict[str, object]:
        try:
            result = self.runner.run_json(
                [self.bin, "agents", "delete", agent_name, "--force", "--json"],
                timeout=self.OPENCLAW_COMMAND_TIMEOUT_SECONDS,
            )
            return {"step": "rollback.agents.delete", "result": result}
        except Exception as exc:
            return {"step": "rollback.agents.delete", "error": str(exc)}

    def _safe_purge_workspace(self, workspace: Path) -> Dict[str, object]:
        try:
            if not workspace.exists():
                return {"step": "rollback.workspace.purge", "result": {"deleted": False, "path": str(workspace)}}
            shutil.rmtree(workspace)
            return {"step": "rollback.workspace.purge", "result": {"deleted": True, "path": str(workspace)}}
        except Exception as exc:
            return {"step": "rollback.workspace.purge", "error": str(exc)}

    def _safe_purge_template_dir(self, template_dir: Path) -> Dict[str, object]:
        try:
            if not template_dir.exists():
                return {"step": "rollback.template.purge", "result": {"deleted": False, "path": str(template_dir)}}
            shutil.rmtree(template_dir)
            return {"step": "rollback.template.purge", "result": {"deleted": True, "path": str(template_dir)}}
        except Exception as exc:
            return {"step": "rollback.template.purge", "error": str(exc)}
