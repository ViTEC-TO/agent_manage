param(
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [Parameter(Mandatory = $true)][string]$ImageTag,
    [string]$TemplateArchive,
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
if ($TemplateIdentify -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $TemplateIdentify -in @('.', '..')) {
    throw "TemplateIdentify must be one safe path segment"
}
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$stagedArchive = Join-Path $PSScriptRoot "templates/$TemplateIdentify.zip"
$sourceArchive = if ([string]::IsNullOrWhiteSpace($TemplateArchive)) { $stagedArchive } else { $TemplateArchive }
if (-not (Test-Path -LiteralPath $sourceArchive -PathType Leaf)) {
    throw "Template archive not found: $sourceArchive"
}
$sourceArchive = (Resolve-Path -LiteralPath $sourceArchive).Path
if ([IO.Path]::GetExtension($sourceArchive) -ne ".zip" -or (Get-Item -LiteralPath $sourceArchive).Length -eq 0) {
    throw "TemplateArchive must be a non-empty zip file"
}

$stagedFullPath = [IO.Path]::GetFullPath($stagedArchive)
$mustStage = -not $sourceArchive.Equals($stagedFullPath, [StringComparison]::OrdinalIgnoreCase)
$backupArchive = $null
if ($mustStage) {
    New-Item -ItemType Directory -Path (Split-Path $stagedFullPath) -Force | Out-Null
    if (Test-Path -LiteralPath $stagedFullPath) {
        $backupArchive = Join-Path ([IO.Path]::GetTempPath()) "$TemplateIdentify-$([Guid]::NewGuid().ToString('N')).zip"
        Move-Item -LiteralPath $stagedFullPath -Destination $backupArchive
    }
    Copy-Item -LiteralPath $sourceArchive -Destination $stagedFullPath
}

try {
    docker buildx build `
        --platform linux/amd64 `
        --load `
        --build-arg "OPENCLAW_IMAGE=$OpenClawImage" `
        --build-arg "OPENCLAW_VERSION=$OpenClawVersion" `
        --build-arg "TEMPLATE_IDENTIFY=$TemplateIdentify" `
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
        --file (Join-Path $PSScriptRoot "Dockerfile") `
        $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Docker image build failed" }

    & (Join-Path $PSScriptRoot "validate-prebuilt-image.ps1") `
        -ImageReference $ImageTag `
        -TemplateIdentify $TemplateIdentify `
        -LayoutProtocolVersion $LayoutProtocolVersion `
        -NginxPort $NginxPort
    if ($LASTEXITCODE -ne 0) { throw "Prebuilt image runtime validation failed" }
}
finally {
    if ($mustStage) {
        if (Test-Path -LiteralPath $stagedFullPath) { Remove-Item -LiteralPath $stagedFullPath -Force }
        if ($null -ne $backupArchive) { Move-Item -LiteralPath $backupArchive -Destination $stagedFullPath }
    }
}
