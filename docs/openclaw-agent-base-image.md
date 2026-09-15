# OpenClaw Agent Base Image

## Purpose

The Agent base image contains Unitag's common OpenClaw runtime but no template-specific Agent.
It is the reusable parent for images that later add one particular template and run `add-agent`.
The existing `container-image/Dockerfile` remains the complete prebuilt-Agent implementation and
is intentionally not replaced by this workflow.

## Included

- Immutable OpenClaw `2026.7.1-1` base image.
- AgentManager under `/opt/unitag/agent-manager`.
- nginx and the shared OpenClaw/nginx entrypoint.
- `@larksuite/cli`.
- Weixin, QQBot and Feishu OpenClaw plugins.
- Shared agent-to-agent, session visibility, subagent and update-check settings.
- A secret-free `/opt/unitag/openclaw-seed` containing only shared runtime defaults.
- Web fetch enabled with `tools.web.fetch.useTrustedEnvProxy=true` so OpenClaw honors trusted proxy environment variables.

## Excluded

- Template archives.
- `openclaw agents add` execution.
- Template or Agent workspaces.
- Gateway Tokens, model keys, permission tickets, channel credentials and auth profiles.

## Build and validate locally

```powershell
.\container-image\build-base-image.ps1 `
  -ImageTag unitag/openclaw-agent-base:2026.7.1-1-v1-local
```

The build automatically runs `validate-base-image.ps1`. Validation starts a restricted temporary
container and checks Gateway HTTP 200, nginx HTTP 200, plugin configuration, secret-free seed
initialization, graceful shutdown and the absence of a prebuilt Agent.

## Publish

Authenticate to the registry first, then run:

```powershell
.\container-image\publish-base-image.ps1 `
  -RegistryRepository sgccr.ccs.tencentyun.com/dola-agent/openclaw-agent-base `
  -Version 1.0.0
```

Use the printed immutable digest as the parent of a template-specific image:

```dockerfile
ARG UNITAG_AGENT_BASE_IMAGE
FROM ${UNITAG_AGENT_BASE_IMAGE}

# Add exactly one template archive and run add-agent in this derived image.
```

Do not use the mutable tag as a production parent and do not add runtime secrets while deriving an
Agent image.

## Build a template-specific Agent image

`Dockerfile.agent` inherits the common base without reinstalling plugins or nginx. It restores the
common seed, copies one template archive, runs `add-agent`, and replaces the image seed with the
result.

```powershell
.\container-image\build-agent-image.ps1 `
  -BaseImageReference unitag/openclaw-agent-base:2026.7.1-1-v1-local `
  -TemplateIdentify dola-agent-trial `
  -TemplateArchive .\container-image\templates\dola-agent-trial.zip `
  -ImageTag unitag/dola-agent-trial:base-v1-local
```

For publication, pass an immutable base digest:

```powershell
.\container-image\publish-agent-image.ps1 `
  -BaseImageReference sgccr.ccs.tencentyun.com/dola-agent/openclaw-agent-base@sha256:<digest> `
  -TemplateIdentify dola-agent-trial `
  -TemplateArchive .\container-image\templates\dola-agent-trial.zip `
  -RegistryRepository sgccr.ccs.tencentyun.com/dola-agent/dola_claw-base `
  -Version 0.4.0
```
