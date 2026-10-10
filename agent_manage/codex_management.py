"""Reversible model configuration and environment-wide Codex OAuth."""

from __future__ import annotations

import json
import stat
import tempfile
from copy import deepcopy
from pathlib import Path

from .refresh_management import _atomic_bytes, _private_json
from .codex_auth import CodexAuthMixin


class CodexManagementMixin(CodexAuthMixin):
    CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
    CODEX_API = "openai-chatgpt-responses"
    # Official Codex models as of 2026-10-10. This is a configuration list,
    # not a claim about the signed-in account's model permissions.
    # https://learn.chatgpt.com/docs/models
    CODEX_DEFAULT_MODEL = "gpt-6.1-sol"
    CODEX_MODELS = (
        ("gpt-6.1-sol", "GPT-6.1 Sol"),
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-6-sol", "GPT-6 Sol"),
        ("gpt-6-luna", "GPT-6 Luna"),
    )

    def _codex_state_path(self):
        return self.config_path.parent / "agent-manage/codex-login.json"

    def _codex_read_state(self):
        path = self._codex_state_path()
        if path.is_symlink():
            raise ValueError("Codex model restore record must not be a link")
        state = json.loads(path.read_text()) if path.exists() else {}
        if state and state.get("config_path") != str(self.config_path):
            raise ValueError("Codex model restore record belongs to another environment")
        return state

    def _codex_models_active(self):
        return bool(self._codex_read_state().get("restore"))

    def _codex_switch_chat_fields(self, target, model):
        saved = []
        for parts in (("model",), ("utilityModel",), ("heartbeat", "model"),
                      ("subagents", "model"), ("compaction", "model")):
            parent = target
            for part in parts[:-1]:
                parent = parent.get(part) if isinstance(parent, dict) else None
            if not isinstance(parent, dict) or (len(parts) > 1 and parts[-1] not in parent):
                continue
            if parts == ("utilityModel",) and "utilityModel" not in parent:
                continue
            saved.append({"path": list(parts), "present": parts[-1] in parent,
                          "value": deepcopy(parent.get(parts[-1]))})
            parent[parts[-1]] = {"primary": model, "fallbacks": []} if parts == ("model",) else model
        return saved

    def _codex_restore_chat_fields(self, target, fields):
        for field in fields:
            parent = target
            for part in field["path"][:-1]:
                parent = parent.setdefault(part, {})
            if field["present"]:
                parent[field["path"][-1]] = field["value"]
            else:
                parent.pop(field["path"][-1], None)

    def _codex_model_definitions(self):
        return [{"id": model_id, "name": name, "api": self.CODEX_API,
                 "baseUrl": self.CODEX_BASE_URL, "agentRuntime": {"id": "openclaw"},
                 "input": ["text", "image"], "reasoning": True}
                for model_id, name in self.CODEX_MODELS]

    def _codex_validate(self, config):
        with tempfile.TemporaryDirectory(prefix="agent-manage-codex-config-") as directory:
            path = Path(directory) / "openclaw.json"
            _private_json(path, config)
            return self.runner.run([self.bin, "config", "validate"], timeout=self.OPENCLAW_COMMAND_TIMEOUT_SECONDS,
                                   env_overrides={"OPENCLAW_CONFIG_PATH": str(path), "OPENCLAW_STATE_DIR": str(self.config_path.parent)})

    def _codex_result(self, state, *, preview=False):
        return {"ok": True, "status": "preview" if preview else "configured", "scope": "environment",
                "mode": "models_only", "skipped": preview, "model": state["selected_model"],
                "models": state["models"], "supported_model_refs": ["openai/" + row["id"] for row in state["models"]],
                "provider_mode": state["provider_mode"], "switched_agents": state["switched_agents"],
                "media": {"image_generation_switched": False, "audio_switched": False},
                "restart_required": False, "gateway_restarted": False, "activation_verified": False}

    def _codex_configure_models(self):
        with self._refresh_lock(self.config_path.parent / "agent-manage"):
            return self._codex_configure_models_locked()

    def _codex_configure_models_locked(self):
        selected = self.CODEX_DEFAULT_MODEL
        state = self._codex_read_state()
        if state.get("restore"):
            if state["status"] != "active":
                raise FileExistsError("Codex model switching was interrupted; run codex-logout to restore")
            state["selected_model"] = self._load_config()["agents"]["defaults"]["model"]["primary"]
            return self._codex_result(state, preview=self.runner.dry_run)

        original = self.config_path.read_bytes()
        original_mode = stat.S_IMODE(self.config_path.stat().st_mode)
        config = self._load_config()
        models = self._codex_model_definitions()
        candidate, restore, hybrid = self._codex_model_candidate(config, models, selected)
        state = {"config_path": str(self.config_path), "status": "applying", "restore": restore,
                 "selected_model": "openai/" + selected, "models": models,
                 "switched_agents": [agent["id"] for agent in restore["agents"]],
                 "provider_mode": "mixed_api_and_codex" if hybrid else "codex"}
        if self.runner.dry_run:
            return self._codex_result(state, preview=True)
        self._codex_validate(candidate)
        if self.config_path.read_bytes() != original:
            raise RuntimeError("Config changed during Codex model switch; retry")
        # Persist the original models first, so an interrupted write is recoverable.
        _private_json(self._codex_state_path(), state)
        try:
            if self.config_path.read_bytes() != original:
                raise RuntimeError("Config changed during Codex model switch; retry")
            _private_json(self.config_path, candidate)
            state["status"] = "active"
            _private_json(self._codex_state_path(), state)
        except Exception:
            encoded = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode()
            if self.config_path.read_bytes() == encoded:
                _atomic_bytes(self.config_path, original, original_mode)
            self._codex_state_path().unlink()
            raise
        return self._codex_result(state)

    def _codex_model_candidate(self, config, models, selected):
        candidate = deepcopy(config)
        providers = candidate.setdefault("models", {}).setdefault("providers", {})
        old_provider = deepcopy(providers.get("openai"))
        refs = {"openai/" + row["id"] for row in models}
        hybrid = bool(old_provider and old_provider.get("apiKey") is not None and old_provider.get("api") != self.CODEX_API)
        if hybrid:
            provider = deepcopy(old_provider)
            provider.update(auth="oauth", models=[row for row in provider.get("models", []) if "openai/" + row["id"] not in refs] + models)
        else:
            if old_provider and old_provider.get("api") != self.CODEX_API:
                raise FileExistsError("Existing OpenAI API provider cannot be safely switched while preserving media routes")
            provider = {**(old_provider or {}), "baseUrl": self.CODEX_BASE_URL, "api": self.CODEX_API,
                        "auth": "oauth", "agentRuntime": {"id": "openclaw"}, "models": models}
            provider.pop("apiKey", None)
        providers["openai"] = provider
        defaults = candidate.setdefault("agents", {}).setdefault("defaults", {})
        old_allowlist = deepcopy(defaults.get("models"))
        defaults["models"] = {ref: {} for ref in sorted(refs)}
        media_refs = self._configured_media_model_refs(config)
        for ref, value in (old_allowlist or {}).items():
            if ref in media_refs:
                defaults["models"][ref] = value
        changed_agents = []
        for agent in candidate["agents"].get("list", []):
            changed_agents.append({"id": agent["id"], "models_present": "models" in agent,
                                   "models": deepcopy(agent.get("models")),
                                   "chat_fields": self._codex_switch_chat_fields(agent, "openai/" + selected)})
            if "models" in agent:
                agent["models"] = {ref: {} for ref in sorted(refs)}
        restore = {"provider_present": old_provider is not None, "provider": old_provider,
                   "allowlist": old_allowlist, "added_refs": sorted(refs), "agents": changed_agents,
                   "default_fields": self._codex_switch_chat_fields(defaults, "openai/" + selected)}
        return candidate, restore, hybrid

    def _codex_restore_models(self):
        with self._refresh_lock(self.config_path.parent / "agent-manage"):
            return self._codex_restore_models_locked()

    def _codex_restore_models_locked(self):
        state = self._codex_read_state()
        result = {"ok": True, "scope": "environment", "status": "restored", "mode": "models_only",
                  "skipped": self.runner.dry_run, "models_restored": False,
                  "restart_required": False, "gateway_restarted": False, "activation_verified": False}
        if not state or self.runner.dry_run:
            return result
        original = self.config_path.read_bytes()
        config = self._load_config()
        restore = state["restore"]
        providers = config.setdefault("models", {}).setdefault("providers", {})
        if restore["provider_present"]:
            providers["openai"] = restore["provider"]
        else:
            providers.pop("openai", None)
        defaults = config.setdefault("agents", {}).setdefault("defaults", {})
        if restore["allowlist"] is None:
            defaults.pop("models", None)
        else:
            defaults["models"] = restore["allowlist"]
        self._codex_restore_chat_fields(defaults, restore["default_fields"])
        for saved in restore["agents"]:
            agent = next((item for item in config["agents"].get("list", []) if item.get("id") == saved["id"]), None)
            if agent is not None:
                self._codex_restore_chat_fields(agent, saved["chat_fields"])
                if saved["models_present"]:
                    agent["models"] = saved["models"]
                else:
                    agent.pop("models", None)
        # New agents inherit restored defaults rather than dangling Codex selectors.
        saved_ids = {saved["id"] for saved in restore["agents"]}
        codex_refs = set(restore["added_refs"])
        for agent in config["agents"].get("list", []):
            if agent.get("id") in saved_ids:
                continue
            for field in self._codex_switch_chat_fields(deepcopy(agent), state["selected_model"]):
                value = field["value"]
                selected = value.get("primary") if isinstance(value, dict) else value
                if selected in codex_refs:
                    self._codex_restore_chat_fields(agent, [{**field, "present": False}])
                elif isinstance(value, dict) and isinstance(value.get("fallbacks"), list):
                    cleaned = {**value, "fallbacks": [ref for ref in value["fallbacks"] if ref not in codex_refs]}
                    self._codex_restore_chat_fields(agent, [{**field, "value": cleaned}])
            if "models" in agent:
                agent["models"] = {key: value for key, value in agent["models"].items() if key not in codex_refs}
                if not agent["models"]:
                    agent.pop("models")
        self._codex_validate(config)
        if self.config_path.read_bytes() != original:
            raise RuntimeError("Config changed during Codex model restore; retry")
        state["status"] = "restoring"
        _private_json(self._codex_state_path(), state)
        _private_json(self.config_path, config)
        self._codex_state_path().unlink()
        result.update(models_restored=True, restored_agents=[agent["id"] for agent in restore["agents"]])
        return result
