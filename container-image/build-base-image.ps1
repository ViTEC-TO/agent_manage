param(
    [Parameter(Mandatory = $true)][string]$ImageTag,
    [string]$OpenClawVersion = "2026.7.1-1",
    [string]$OpenClawImage = "ghcr.io/openclaw/openclaw@sha256:2f5ce8848a1a69b3c460622e566cb9395da9fd18d7ef7b038cd8e2c4f195decf",
    [string]$AgentManagerVersion = "0.5.0",
    [string]$LayoutProtocolVersion = "2",
    [string]$LarkSuiteCliVersion = "1.0.93",
    [string]$WeixinPluginVersion = "2.4.8",
    [string]$QqBotPluginVersion = "2026.7.1",
    [string]$FeishuPluginVersion = "2026.7.1",
    [int]$NginxPort = 80,
    [string]$DebianMirror = "http://deb.debian.org/debian",
    [string]$DebianSecurityMirror = "http://deb.debian.org/debian-security"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

docker buildx build `
    --platform linux/amd64 `
    --load `
    --build-arg "OPENCLAW_IMAGE=$OpenClawImage" `
    --build-arg "OPENCLAW_VERSION=$OpenClawVersion" `
    --build-arg "AGENT_MANAGER_VERSION=$AgentManagerVersion" `
    --build-arg "LAYOUT_PROTOCOL_VERSION=$LayoutProtocolVersion" `
    --build-arg "LARKSUITE_CLI_VERSION=$LarkSuiteCliVersion" `
    --build-arg "WEIXIN_PLUGIN_VERSION=$WeixinPluginVersion" `
    --build-arg "QQBOT_PLUGIN_VERSION=$QqBotPluginVersion" `
    --build-arg "FEISHU_PLUGIN_VERSION=$FeishuPluginVersion" `
    --build-arg "NGINX_PORT=$NginxPort" `
    --build-arg "DEBIAN_MIRROR=$DebianMirror" `
    --build-arg "DEBIAN_SECURITY_MIRROR=$DebianSecurityMirror" `
    --tag $ImageTag `
    --file (Join-Path $PSScriptRoot "Dockerfile.base") `
    $repoRoot
if ($LASTEXITCODE -ne 0) { throw "Docker base image build failed" }

& (Join-Path $PSScriptRoot "validate-base-image.ps1") `
    -ImageReference $ImageTag `
    -LayoutProtocolVersion $LayoutProtocolVersion `
    -NginxPort $NginxPort
if ($LASTEXITCODE -ne 0) { throw "Base image runtime validation failed" }
