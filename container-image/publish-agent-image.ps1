param(
    [Parameter(Mandatory = $true)][string]$BaseImageReference,
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [Parameter(Mandatory = $true)][string]$TemplateArchive,
    [Parameter(Mandatory = $true)][string]$RegistryRepository,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$LayoutProtocolVersion = "2"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$') { throw "Version is invalid" }
$tag = "${RegistryRepository}:${TemplateIdentify}-${Version}-amd64"

& (Join-Path $PSScriptRoot "build-agent-image.ps1") `
    -BaseImageReference $BaseImageReference `
    -TemplateIdentify $TemplateIdentify `
    -TemplateArchive $TemplateArchive `
    -ImageTag $tag `
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
