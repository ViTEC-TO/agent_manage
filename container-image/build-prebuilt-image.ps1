param(
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [Parameter(Mandatory = $true)][string]$ImageTag,
    [string]$OpenClawVersion = "2026.7.1-1",
    [string]$OpenClawImage = "ghcr.io/openclaw/openclaw@sha256:2f5ce8848a1a69b3c460622e566cb9395da9fd18d7ef7b038cd8e2c4f195decf"
)

$ErrorActionPreference = "Stop"
if ($TemplateIdentify -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $TemplateIdentify -in @('.', '..')) {
    throw "TemplateIdentify must be one safe path segment"
}
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$templateArchive = Join-Path $PSScriptRoot "templates/$TemplateIdentify.zip"
if (-not (Test-Path -LiteralPath $templateArchive -PathType Leaf)) {
    throw "Template archive not found: $templateArchive"
}

docker buildx build `
    --platform linux/amd64 `
    --load `
    --build-arg "OPENCLAW_IMAGE=$OpenClawImage" `
    --build-arg "OPENCLAW_VERSION=$OpenClawVersion" `
    --build-arg "TEMPLATE_IDENTIFY=$TemplateIdentify" `
    --build-arg "AGENT_MANAGER_VERSION=0.5.0" `
    --build-arg "LAYOUT_PROTOCOL_VERSION=1" `
    --tag $ImageTag `
    --file (Join-Path $PSScriptRoot "Dockerfile") `
    $repoRoot
