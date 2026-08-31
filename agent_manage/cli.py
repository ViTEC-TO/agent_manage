from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

from . import __version__
from .local import LocalRunner
from .models import (
    AddAgentRequest,
    AddAgentsRequest,
    AddFeishuBotRequest,
    AddTelegramBotRequest,
    AddWeixinBotRequest,
    CreateInstanceRequest,
    DeleteFeishuBotRequest,
    DeleteTelegramBotRequest,
    DeleteWeixinBotRequest,
    SetModelRequest,
)
from .orchestrator import InstanceManagerV2
from .response import (
    CliArgumentError,
    JsonArgumentParser,
    build_error_response,
    build_success_response,
    print_json,
    redact_sensitive_values,
)

MODEL_ENV_CHOICES = sorted(InstanceManagerV2.MODEL_GATEWAYS.keys())
DEFAULT_MODEL_ENV = InstanceManagerV2.DEFAULT_MODEL_ENV
DEFAULT_AI_SHOP = InstanceManagerV2.DEFAULT_AI_SHOP
DEFAULT_IMAGE_QUALITY = InstanceManagerV2.DEFAULT_IMAGE_QUALITY
IMAGE_QUALITY_CHOICES = InstanceManagerV2.IMAGE_QUALITY_CHOICES


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = JsonArgumentParser(prog="agent-manage")
    parser.add_argument("--openclaw-bin", default="openclaw")
    parser.add_argument("--project-dir")
    parser.add_argument("--template-root")
    parser.add_argument("--config-path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_instance = subparsers.add_parser("create-instance")
    create_instance.add_argument("--template-name")
    create_instance.add_argument("--agent-zip")
    create_instance.add_argument("--local", action="store_true")
    create_instance_model_key = create_instance.add_mutually_exclusive_group(required=True)
    create_instance_model_key.add_argument("--model-key")
    create_instance_model_key.add_argument("--model-key-stdin", action="store_true")
    create_instance.add_argument(
        "--model-env",
        choices=MODEL_ENV_CHOICES,
        default=DEFAULT_MODEL_ENV,
    )
    create_instance.add_argument(
        "--ai-shop",
        default=DEFAULT_AI_SHOP,
        help=f"model shop path (default: {DEFAULT_AI_SHOP})",
    )
    create_instance.add_argument("--model")
    create_instance.add_argument(
        "--image-quality",
        choices=IMAGE_QUALITY_CHOICES,
        default=DEFAULT_IMAGE_QUALITY,
    )
    create_instance.add_argument("--workspace-root")
    create_instance.add_argument("--no-rollback", action="store_true")

    add_agents = subparsers.add_parser("add-agents")
    add_agents.add_argument("--agents", required=True)
    add_agents.add_argument("--workspace-root", default="~/data")

    add_tg_bot = subparsers.add_parser("add-tg-bot")
    add_tg_bot.add_argument("--agent", required=True)
    add_tg_bot_token = add_tg_bot.add_mutually_exclusive_group(required=True)
    add_tg_bot_token.add_argument("--tg-token")
    add_tg_bot_token.add_argument("--tg-token-stdin", action="store_true")
    add_tg_bot.add_argument("--bot-name")

    add_feishu_bot = subparsers.add_parser("add-feishu-bot")
    add_feishu_bot.add_argument("--agent", required=True)
    add_feishu_bot.add_argument("--domain", choices=["feishu", "lark"], default="feishu")
    add_feishu_bot.add_argument("--account-id", default="main")
    add_feishu_bot.add_argument("--app-id", required=True)
    add_feishu_bot_secret = add_feishu_bot.add_mutually_exclusive_group(required=True)
    add_feishu_bot_secret.add_argument("--app-secret")
    add_feishu_bot_secret.add_argument("--app-secret-stdin", action="store_true")
    add_feishu_bot.add_argument("--bot-name")
    add_feishu_bot.add_argument("--dm-policy", default="open")
    add_feishu_bot.add_argument("--allow-from", action="append")
    add_feishu_bot.add_argument("--bind-lark-cli", action="store_true")
    add_feishu_bot.add_argument(
        "--lark-cli-identity",
        choices=["bot-only", "user-default"],
        default="bot-only",
    )

    add_weixin_bot = subparsers.add_parser("add-weixin-bot")
    add_weixin_bot.add_argument("--agent", required=True)
    add_weixin_bot.add_argument("--ilink-bot-id", required=True)
    add_weixin_bot_token = add_weixin_bot.add_mutually_exclusive_group(required=True)
    add_weixin_bot_token.add_argument("--bot-token")
    add_weixin_bot_token.add_argument("--bot-token-stdin", action="store_true")
    add_weixin_bot.add_argument("--baseurl")
    add_weixin_bot.add_argument("--ilink-user-id")
    add_weixin_bot.add_argument("--bot-name")
    add_weixin_bot.add_argument("--route-tag")
    add_weixin_bot.add_argument("--cdn-base-url")

    subparsers.add_parser("check-server-status")

    subparsers.add_parser("tg-bot-status")

    subparsers.add_parser("feishu-bot-status")

    subparsers.add_parser("weixin-bot-status")

    delete_tg_bot = subparsers.add_parser("delete-tg-bot")
    delete_tg_bot.add_argument("--bot-name", required=True)

    delete_feishu_bot = subparsers.add_parser("delete-feishu-bot")
    delete_feishu_bot.add_argument("--account-id", required=True)

    delete_weixin_bot = subparsers.add_parser("delete-weixin-bot")
    delete_weixin_bot.add_argument("--ilink-bot-id", required=True)

    subparsers.add_parser("agents-list")

    set_model = subparsers.add_parser("set-model")
    set_model.add_argument("--model", required=True)

    subparsers.add_parser("current-model")

    subparsers.add_parser("models")

    subparsers.add_parser("update-model")

    subparsers.add_parser("current-gateway-token")

    sensitive_values: List[str] = []
    try:
        args = parser.parse_args(argv)
        _reject_container_path_overrides(raw_argv)
        client = InstanceManagerV2(
            LocalRunner(
                openclaw_bin=args.openclaw_bin,
                project_dir=args.project_dir,
                dry_run=args.dry_run,
            ),
            template_root=args.template_root,
            config_path=args.config_path,
        )

        if args.command == "create-instance":
            result = client.create_instance(
                CreateInstanceRequest(
                    template_name=args.template_name,
                    model_key=_secret_argument(
                        args.model_key, args.model_key_stdin, sensitive_values
                    ),
                    model_env=args.model_env,
                    ai_shop=args.ai_shop,
                    model=args.model,
                    image_quality=args.image_quality,
                    workspace_root=args.workspace_root
                    or ("~/.openclaw/data" if args.local else "~/data"),
                    rollback_on_fail=not args.no_rollback,
                    agent_zip=args.agent_zip,
                    local=args.local,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "add-agents":
            result = client.add_agents(
                AddAgentsRequest(
                    agents=_parse_add_agents(args.agents),
                    workspace_root=args.workspace_root,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "add-tg-bot":
            result = client.add_tg_bot(
                AddTelegramBotRequest(
                    agent_name=args.agent,
                    bot_token=_secret_argument(
                        args.tg_token, args.tg_token_stdin, sensitive_values
                    ),
                    bot_name=args.bot_name,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "add-feishu-bot":
            result = client.add_feishu_bot(
                AddFeishuBotRequest(
                    agent_name=args.agent,
                    domain=args.domain,
                    account_id=args.account_id,
                    app_id=args.app_id,
                    app_secret=_secret_argument(
                        args.app_secret, args.app_secret_stdin, sensitive_values
                    ),
                    bot_name=args.bot_name,
                    dm_policy=args.dm_policy,
                    allow_from=args.allow_from,
                    bind_lark_cli=args.bind_lark_cli,
                    lark_cli_identity=args.lark_cli_identity,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "add-weixin-bot":
            result = client.add_weixin_bot(
                AddWeixinBotRequest(
                    agent_name=args.agent,
                    ilink_bot_id=args.ilink_bot_id,
                    bot_token=_secret_argument(
                        args.bot_token, args.bot_token_stdin, sensitive_values
                    ),
                    baseurl=args.baseurl,
                    ilink_user_id=args.ilink_user_id,
                    bot_name=args.bot_name,
                    route_tag=args.route_tag,
                    cdn_base_url=args.cdn_base_url,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "check-server-status":
            result = client.check_server_status()
            print_json(_success_response(result, client))
            return 0
        if args.command == "tg-bot-status":
            result = client.get_tg_bot_status()
            print_json(_success_response(result, client))
            return 0
        if args.command == "feishu-bot-status":
            result = client.get_feishu_bot_status()
            print_json(_success_response(result, client))
            return 0
        if args.command == "weixin-bot-status":
            result = client.get_weixin_bot_status()
            print_json(_success_response(result, client))
            return 0
        if args.command == "delete-tg-bot":
            result = client.delete_tg_bot(
                DeleteTelegramBotRequest(
                    bot_name=args.bot_name,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "delete-feishu-bot":
            result = client.delete_feishu_bot(
                DeleteFeishuBotRequest(
                    account_id=args.account_id,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "delete-weixin-bot":
            result = client.delete_weixin_bot(
                DeleteWeixinBotRequest(
                    ilink_bot_id=args.ilink_bot_id,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "agents-list":
            result = client.list_agents()
            print_json(_success_response(result, client))
            return 0
        if args.command == "set-model":
            result = client.set_model(
                SetModelRequest(
                    model_ref=args.model,
                )
            )
            print_json(_success_response(result, client))
            return 0
        if args.command == "current-model":
            result = client.get_current_model()
            print_json(_success_response(result, client))
            return 0
        if args.command == "models":
            result = client.get_supported_models()
            print_json(_success_response(result, client))
            return 0
        if args.command == "update-model":
            result = client.update_model_catalog()
            print_json(_success_response(result, client))
            return 0
        if args.command == "current-gateway-token":
            result = client.get_current_gateway_token()
            print_json(_success_response(result, client))
            return 0

        parser.print_help(sys.stderr)
        return 1
    except SystemExit:
        raise
    except Exception as exc:
        print_json(redact_sensitive_values(build_error_response(exc), sensitive_values))
        return 1


def _parse_add_agents(raw: str) -> List[AddAgentRequest]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("--agents must be a JSON array") from exc

    if not isinstance(payload, list):
        raise ValueError("--agents must be a JSON array")
    if not payload:
        raise ValueError("--agents must contain at least one item")

    agents: List[AddAgentRequest] = []
    for index, item in enumerate(payload):
        if isinstance(item, str):
            agent_name = item.strip()
            if not agent_name:
                raise ValueError(f"agents[{index}] must not be empty")
            agents.append(AddAgentRequest(agent_name=agent_name))
            continue

        if not isinstance(item, dict):
            raise ValueError(f"agents[{index}] must be a string or object")

        agent_name = item.get("agent_name")
        template_name = item.get("template_name")
        workspace = item.get("workspace")
        model = item.get("model")

        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError(f"agents[{index}].agent_name is required")
        if template_name is not None and not isinstance(template_name, str):
            raise ValueError(f"agents[{index}].template_name must be a string")
        if workspace is not None and not isinstance(workspace, str):
            raise ValueError(f"agents[{index}].workspace must be a string")
        if model is not None and not isinstance(model, str):
            raise ValueError(f"agents[{index}].model must be a string")

        agents.append(
            AddAgentRequest(
                agent_name=agent_name.strip(),
                template_name=template_name.strip() if isinstance(template_name, str) else None,
                workspace=workspace.strip() if isinstance(workspace, str) else None,
                model=model.strip() if isinstance(model, str) else None,
            )
        )
    return agents


def _read_secret_from_stdin() -> str:
    value = sys.stdin.read()
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\r") or value.endswith("\n"):
        return value[:-1]
    return value


def _secret_argument(
    plaintext: Optional[str], from_stdin: bool, sensitive_values: List[str]
) -> str:
    value = _read_secret_from_stdin() if from_stdin else (plaintext or "")
    if value:
        sensitive_values.append(value)
        return value
    raise ValueError("Secret value must not be empty")


def _success_response(result: object, client: InstanceManagerV2) -> dict:
    result_restart_required = (
        isinstance(result, dict) and bool(result.get("restart_required", False))
    )
    return build_success_response(
        result,
        restart_required=bool(client.restart_required or result_restart_required),
    )


def _reject_container_path_overrides(argv: List[str]) -> None:
    if os.environ.get("UNITAG_AGENT_MANAGER_RUNTIME") != "container":
        return
    forbidden = ("--openclaw-bin", "--project-dir", "--template-root", "--config-path")
    for option in forbidden:
        if option in argv or any(argument.startswith(f"{option}=") for argument in argv):
            raise CliArgumentError(f"{option} is not allowed in container runtime")


if __name__ == "__main__":
    raise SystemExit(main())
