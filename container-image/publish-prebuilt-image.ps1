param(
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [Parameter(Mandatory = $true)][string]$TemplateArchive,
    [Parameter(Mandatory = $true)][string]$RegistryRepository,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OpenClawVersion = "2026.7.1-1",
    [string]$OpenClawImage = "ghcr.io/openclaw/openclaw@sha256:2f5ce8848a1a69b3c460622e566cb9395da9fd18d7ef7b038cd8e2c4f195decf",
    [string]$AgentManagerVersion = "0.5.0",
    [string]$LayoutProtocolVersion = "2"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$') { throw "Version is invalid" }
$tag = "${RegistryRepository}:${TemplateIdentify}-${Version}-amd64"

& (Join-Path $PSScriptRoot "build-prebuilt-image.ps1") `
    -TemplateIdentify $TemplateIdentify `
    -TemplateArchive $TemplateArchive `
    -ImageTag $tag `
    -OpenClawVersion $OpenClawVersion `
    -OpenClawImage $OpenClawImage `
    -AgentManagerVersion $AgentManagerVersion `
    -LayoutProtocolVersion $LayoutProtocolVersion
if ($LASTEXITCODE -ne 0) { throw "Build or validation failed; image was not pushed" }

docker push $tag
if ($LASTEXITCODE -ne 0) { throw "Image push failed" }
docker pull $tag | Out-Null
$image = docker image inspect $tag | ConvertFrom-Json
$immutableReference = @($image[0].RepoDigests | Where-Object { $_.StartsWith("$RegistryRepository@sha256:") })[0]
if ([string]::IsNullOrWhiteSpace($immutableReference)) { throw "Registry digest was not resolved after push" }
Write-Output "PublishedTag=$tag"
Write-Output "ImmutableReference=$immutableReference"
