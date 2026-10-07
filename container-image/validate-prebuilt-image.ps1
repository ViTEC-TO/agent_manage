param(
    [Parameter(Mandatory = $true)][string]$ImageReference,
    [Parameter(Mandatory = $true)][string]$TemplateIdentify,
    [string]$LayoutProtocolVersion = "2",
    [int]$NginxPort = 80,
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
if ($labels.'io.dola.unitag.nginx-port' -ne $NginxPort.ToString()) { throw "nginx port label mismatch" }

docker run --rm --entrypoint npm $ImageReference list --global --depth=0 '@larksuite/cli'
if ($LASTEXITCODE -ne 0) { throw "@larksuite/cli is missing from the image" }

$containerName = "unitag-prebuilt-validation-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
$runtimeToken = "unitag-runtime-validation-token"
$started = $false
$portListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$portListener.Start()
$hostPort = $portListener.LocalEndpoint.Port
$portListener.Stop()
$nginxPortListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$nginxPortListener.Start()
$nginxHostPort = $nginxPortListener.LocalEndpoint.Port
$nginxPortListener.Stop()
$startupScript = @'
exec /usr/local/bin/unitag-openclaw-entrypoint node openclaw.mjs gateway --bind lan
'@

try {
    $runArguments = @(
        "run", "--detach", "--name", $containerName,
        "--user", "node",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--mount", "type=tmpfs,destination=/home/node/.openclaw,tmpfs-mode=1777",
        "--env", "OPENCLAW_GATEWAY_TOKEN=$runtimeToken",
        "--env", "UNITAG_NGINX_PORT=$NginxPort",
        "--publish", "127.0.0.1:${hostPort}:18789",
        "--publish", "127.0.0.1:${nginxHostPort}:$NginxPort",
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

    $staticContent = "unitag-nginx-$([Guid]::NewGuid().ToString('N'))"
    & docker exec $containerName sh -c "printf '%s' '$staticContent' > /home/node/.openclaw/workspace/public/index.html"
    if ($LASTEXITCODE -ne 0) { throw "Unable to write nginx validation content" }
    $nginxResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$nginxHostPort/" -TimeoutSec 10
    if ($nginxResponse.StatusCode -ne 200 -or $nginxResponse.Content -ne $staticContent) {
        throw "nginx static content validation failed"
    }

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
assert "main" in config["tools"]["agentToAgent"]["allow"]
assert "__TEMPLATE_IDENTIFY__" in config["tools"]["agentToAgent"]["allow"]
assert config["tools"]["sessions"]["visibility"] == "all"
assert config["tools"]["web"]["fetch"]["enabled"] is True
assert config["tools"]["web"]["fetch"]["useTrustedEnvProxy"] is True
assert config["agents"]["defaults"]["subagents"]["allowAgents"] == ["*"]
assert config["update"]["checkOnStart"] is False
assert not config["gateway"]["auth"].get("token")
assert Path("/home/node/.openclaw/.unitag-seed-initialized").is_file()
nginx_config = Path("/home/node/.openclaw/nginx/nginx.conf").read_text()
assert "listen __NGINX_PORT__ default_server;" in nginx_config
assert "root /home/node/.openclaw/workspace/public;" in nginx_config
assert "proxy_pass" not in nginx_config
assert Path("/tmp/nginx/nginx.pid").is_file()
'@.Replace("__TEMPLATE_IDENTIFY__", $TemplateIdentify).Replace("__NGINX_PORT__", $NginxPort.ToString())
    & docker exec $containerName python3 -c $validationCode
    if ($LASTEXITCODE -ne 0) { throw "Seed/runtime configuration validation failed" }

    docker stop --time 10 $containerName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Container runtime did not stop after SIGTERM" }
    $exitCode = docker inspect --format '{{.State.ExitCode}}' $containerName
    if ($LASTEXITCODE -ne 0 -or $exitCode -ne "0") {
        docker logs --tail 100 $containerName
        throw "Container runtime did not exit cleanly after SIGTERM"
    }
    Write-Output "Validated prebuilt image: $ImageReference"
    Write-Output "TemplateIdentify=$TemplateIdentify LayoutProtocolVersion=$LayoutProtocolVersion GatewayHttp=200 NginxHttp=200 NginxPort=$NginxPort RestrictedRuntime=true GracefulStop=true ExtensionsConfigured=true"
}
finally {
    if ($started) { docker rm --force $containerName | Out-Null }
}
