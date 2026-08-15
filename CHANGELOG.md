# Changelog

## Unreleased

- 创建和更新模型目录时，普通模型列表只保留 `chat` 分类，排除图片、视频和音频模型。
- 创建实例时固定配置 `openai/gpt-image-2`，支持 `--image-quality`，并通过 workspace 受管规则把图片生成质量默认设为 `low`。

## 0.2.1 - 2026-05-06

- `create-instance` 和 `add-agents` 支持模板声明的 `requiredLibraries` 依赖检查/安装，以及 `common-skills` 全局 skill 同步。

## 0.2.0 - 2026-04-20

- 新增 `add-agents` 命令，支持用 JSON 数组为已启动服务批量追加 agent。
