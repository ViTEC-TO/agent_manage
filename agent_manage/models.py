from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CreateInstanceRequest:
    template_name: str = ""
    model_key: str = ""
    model_env: str = "global"
    ai_shop: str | None = None
    model: str | None = None
    image_quality: str = "low"
    workspace_root: str = "~/data"
    rollback_on_fail: bool = True
    agent_zip: str | None = None
    local: bool = False


@dataclass
class AddAgentRequest:
    agent_name: str
    template_name: str | None = None
    workspace: str | None = None
    model: str | None = None


@dataclass
class AddAgentsRequest:
    agents: list[AddAgentRequest]
    workspace_root: str = "~/data"


@dataclass
class AddTelegramBotRequest:
    agent_name: str
    bot_token: str
    bot_name: str | None = None


@dataclass
class AddFeishuBotRequest:
    agent_name: str
    app_id: str
    app_secret: str
    domain: str = "feishu"
    account_id: str = "main"
    bot_name: str | None = None
    dm_policy: str = "open"
    allow_from: list[str] | None = None
    bind_lark_cli: bool = False
    lark_cli_identity: str = "bot-only"


@dataclass
class AddWeixinBotRequest:
    agent_name: str
    ilink_bot_id: str
    bot_token: str
    baseurl: str | None = None
    ilink_user_id: str | None = None
    bot_name: str | None = None
    route_tag: str | None = None
    cdn_base_url: str | None = None


@dataclass
class DeleteTelegramBotRequest:
    bot_name: str


@dataclass
class DeleteFeishuBotRequest:
    account_id: str


@dataclass
class DeleteWeixinBotRequest:
    ilink_bot_id: str


@dataclass
class SetModelRequest:
    model_ref: str
