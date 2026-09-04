param(
    [Parameter(Mandatory = $true)][string]$ImageReference,
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [string]$LayoutProtocolVersion = "2",
    [int]$ReadinessTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
if ($TemplateIdentify -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $TemplateIdentify -in @('.', '..')) {
    throw "TemplateIdentify must be one safe path segment"
}

$inspection = docker image inspect $ImageReference | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $inspection.Count -ne 1) { throw "Unable to inspect image: $ImageReference" }
$labels = $inspection[0].Config.Labels
if ($labels.'io.dola.unitag.template-identify' -ne $TemplateIdentify) { throw "Template label mismatch" }
if ($labels.'io.dola.unitag.layout-protocol-version' -ne $LayoutProtocolVersion) { throw "Layout protocol label mismatch" }

docker run --rm --entrypoint npm $ImageReference list --global --depth=0 '@larksuite/cli'
if ($LASTEXITCODE -ne 0) { throw "@larksuite/cli is missing from the image" }

$containerName = "unitag-prebuilt-validation-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
$runtimeToken = "unitag-runtime-validation-token"
$started = $false
$portListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$portListener.Start()
$hostPort = $portListener.LocalEndpoint.Port
$portListener.Stop()
$startupScript = @'
exec /usr/local/bin/unitag-openclaw-entrypoint node openclaw.mjs gateway --bind lan
'@

try {
    $runArguments = @(
        "run", "--detach", "--name", $containerName,
        "--mount", "type=tmpfs,destination=/home/node/.openclaw,tmpfs-mode=1777",
        "--env", "OPENCLAW_GATEWAY_TOKEN=$runtimeToken",
        "--publish", "127.0.0.1:${hostPort}:18789",
        "--entrypoint", "sh", $ImageReference, "-c", $startupScript
    )
    & docker @runArguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to start validation container" }
    $started = $true

    $deadline = [DateTime]::UtcNow.AddSeconds($ReadinessTimeoutSeconds)
    do {
        $running = docker inspect --format '{{.State.Running}}' $containerName
        if ($LASTEXITCODE -ne 0 -or $running -ne "true") {
            docker logs --tail 100 $containerName
            throw "Gateway validation container exited before becoming ready"
        }
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$hostPort/" -SkipHttpErrorCheck -TimeoutSec 5
            if ($response.StatusCode -eq 200) { break }
        }
        catch { }
        if ([DateTime]::UtcNow -ge $deadline) {
            docker logs --tail 100 $containerName
            throw "Gateway did not reach HTTP 200 within $ReadinessTimeoutSeconds seconds"
        }
        Start-Sleep -Seconds 2
    } while ($true)

    $runtimeConfigCode = @'
import json
from pathlib import Path
path = Path("/home/node/.openclaw/openclaw.json")
config = json.loads(path.read_text())
control_ui = config.setdefault("gateway", {}).setdefault("controlUi", {})
control_ui["allowedOrigins"] = ["https://validation.invalid"]
path.write_text(json.dumps(config))
'@
    & docker exec $containerName python3 -c $runtimeConfigCode
    if ($LASTEXITCODE -ne 0) { throw "Unable to apply runtime configuration to validation container" }
    Start-Sleep -Seconds 2

    $validationCode = @'
import json
from pathlib import Path
config = json.loads(Path("/home/node/.openclaw/openclaw.json").read_text())
assert config["gateway"]["mode"] == "local"
assert config["gateway"]["auth"]["mode"] == "token"
assert config["gateway"]["controlUi"]["allowedOrigins"] == ["https://validation.invalid"]
assert any(agent.get("id") == "__TEMPLATE_IDENTIFY__" for agent in config["agents"]["list"])
assert config["plugins"]["entries"]["openclaw-weixin"]["enabled"] is True
assert config["tools"]["agentToAgent"]["enabled"] is True
assert config["tools"]["agentToAgent"]["allow"] == []
assert config["tools"]["sessions"]["visibility"] == "all"
assert config["agents"]["defaults"]["subagents"]["allowAgents"] == ["*"]
assert config["update"]["checkOnStart"] is False
assert not config["gateway"]["auth"].get("token")
assert Path("/home/node/.openclaw/.unitag-seed-initialized").is_file()
'@.Replace("__TEMPLATE_IDENTIFY__", $TemplateIdentify)
    & docker exec $containerName python3 -c $validationCode
    if ($LASTEXITCODE -ne 0) { throw "Seed/runtime configuration validation failed" }
    Write-Output "Validated prebuilt image: $ImageReference"
    Write-Output "TemplateIdentify=$TemplateIdentify LayoutProtocolVersion=$LayoutProtocolVersion GatewayHttp=200 ExtensionsConfigured=true"
}
finally {
    if ($started) { docker rm --force $containerName | Out-Null }
}
