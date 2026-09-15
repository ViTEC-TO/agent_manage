param(
    [Parameter(Mandatory = $true)][string]$BaseImageReference,
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [Parameter(Mandatory = $true)][string]$ImageTag,
    [string]$TemplateArchive,
    [string]$LayoutProtocolVersion = "2",
    [int]$NginxPort = 80
)

$ErrorActionPreference = "Stop"
if ($TemplateIdentify -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $TemplateIdentify -in @('.', '..')) {
    throw "TemplateIdentify must be one safe path segment"
}
if ($BaseImageReference -notmatch '^.+(?:@sha256:[0-9a-f]{64}|:[A-Za-z0-9][A-Za-z0-9._-]*)$') {
    throw "BaseImageReference must be a Docker image tag or immutable sha256 reference"
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
        --build-arg "UNITAG_AGENT_BASE_IMAGE=$BaseImageReference" `
        --build-arg "TEMPLATE_IDENTIFY=$TemplateIdentify" `
        --tag $ImageTag `
        --file (Join-Path $PSScriptRoot "Dockerfile.agent") `
        $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Docker Agent image build failed" }

    & (Join-Path $PSScriptRoot "validate-prebuilt-image.ps1") `
        -ImageReference $ImageTag `
        -TemplateIdentify $TemplateIdentify `
        -LayoutProtocolVersion $LayoutProtocolVersion `
        -NginxPort $NginxPort
    if ($LASTEXITCODE -ne 0) { throw "Agent image runtime validation failed" }
}
finally {
    if ($mustStage) {
        if (Test-Path -LiteralPath $stagedFullPath) { Remove-Item -LiteralPath $stagedFullPath -Force }
        if ($null -ne $backupArchive) { Move-Item -LiteralPath $backupArchive -Destination $stagedFullPath }
    }
}
