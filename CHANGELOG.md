# Changelog

## 0.4.0 - 2026-08-22

- 将 3000 多行单体管理器拆分为模型、模板与 workspace、渠道、Gateway、公共核心和顶层编排模块；`InstanceManagerV2`、CLI、方法名和返回结构保持兼容。
- 新增集中式模型网关配置与 URL 构造模块，三个环境域名各保留一个定义；相关 URL 测试独立管理。
- `--ai-shop` 默认值统一为 `shop`，未传时模型目录使用 `/byProvider/shop`，模型调用地址使用 `/aigateway/shop/v1`。
- 项目 Python 版本声明调整为 `>=3.10`，与现有类型语法保持一致。
- 增加模板名称、路径边界、压缩包链接/越界/体积校验与原子目录替换，防止模板包写出目标目录或误删已有 workspace。
- 同名 agent 重跑 `create-instance` 时保留已有 Gateway Token 和仍受支持的默认模型，并明确返回 `mode: reconciled`。
- 修复 provider 已包含在模型 ID 中时生成重复模型前缀、无 PyYAML 时字符串 Skill 列表漏解析、Gateway 端口子串误判等边界问题。
- 配置写入增加临时文件清理、落盘同步和 `0600` 权限，创建流程失败时恢复操作前配置快照；执行环境补齐 system sbin PATH，关键 OpenClaw 命令设置超时。

## 0.3.3 - 2026-08-22

- 新增 `global` 模型环境，使用 `https://api.dola.io`；未传 `--model-env` 时默认使用 `global`，原有 `test`、`cn` 地址保持不变。

## 0.3.2 - 2026-08-18

- 新增内置公共 Skill `nginx-delivery`，并将精简运行规则同步到创建及批量追加的全部 agent workspace；旧图片规则区块会自动迁移。
- 兼容 npm stable OpenClaw `2026.7.1-2`，写入模型配置时仅保留 `text`、`image` 输入类型，避免 `video`、`audio` 导致模型 schema 校验失败。

## 0.3.1 - 2026-08-16

- 修复与 npm stable OpenClaw `2026.7.1-2` 的配置兼容性：图片生成模型改写到 `agents.defaults.imageGenerationModel`。
- 创建或更新模型配置时移除此前写入、会被 stable 判为未知字段的 `agents.defaults.mediaModels`。
- 模型列表读取同时识别 stable 的独立媒体模型字段和新版 `mediaModels` 结构，继续排除媒体生成模型。
- 图片路由按商店目录判断：官方 `openai` provider 包含 `gpt-image-2` 时沿用当前商店（无需 `modelCategory`），否则图片使用的 `openai` provider 回退到同环境的无商店 `/aigateway/v1`；其他 provider 仍沿用当前商店。
- 修复 `update-model` 重新拉取目录时丢失已有商店路径的问题，并避免让图片 provider 的无商店回退地址覆盖实例商店判断。

## 0.3.0 - 2026-08-16

- 创建和更新模型目录时，普通模型列表只保留 `chat` 分类，排除图片、视频和音频模型。
- 创建实例时固定配置 `openai/gpt-image-2`，支持 `--image-quality`，并通过 workspace 受管规则把图片生成质量默认设为 `low`。
- 新增统一版本来源和 `agent-manage --version` / `scripts/agentctl.py --version` 查询入口。

## 0.2.1 - 2026-05-06

- `create-instance` 和 `add-agents` 支持模板声明的 `requiredLibraries` 依赖检查/安装，以及 `common-skills` 全局 skill 同步。

## 0.2.0 - 2026-04-20

- 新增 `add-agents` 命令，支持用 JSON 数组为已启动服务批量追加 agent。
