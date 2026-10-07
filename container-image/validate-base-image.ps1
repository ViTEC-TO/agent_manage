param(
    [Parameter(Mandatory = $true)][string]$ImageReference,
    [string]$LayoutProtocolVersion = "2",
    [int]$NginxPort = 80,
    [int]$ReadinessTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$inspection = docker image inspect $ImageReference | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $inspection.Count -ne 1) { throw "Unable to inspect image: $ImageReference" }
$labels = $inspection[0].Config.Labels
if ($labels.'io.dola.unitag.image-kind' -ne "agent-base") { throw "Base image kind label mismatch" }
if ($labels.'io.dola.unitag.layout-protocol-version' -ne $LayoutProtocolVersion) { throw "Layout protocol label mismatch" }
if ($labels.'io.dola.unitag.nginx-port' -ne $NginxPort.ToString()) { throw "nginx port label mismatch" }
if ($null -ne $labels.'io.dola.unitag.template-identify') { throw "Base image must not identify a template" }

docker run --rm --entrypoint npm $ImageReference list --global --depth=0 '@larksuite/cli'
if ($LASTEXITCODE -ne 0) { throw "@larksuite/cli is missing from the image" }

$containerName = "unitag-base-validation-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
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
    & docker exec $containerName sh -c "mkdir -p /home/node/.openclaw/workspace/public && printf '%s' '$staticContent' > /home/node/.openclaw/workspace/public/index.html"
    if ($LASTEXITCODE -ne 0) { throw "Unable to write nginx validation content" }
    $nginxResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$nginxHostPort/" -TimeoutSec 10
    if ($nginxResponse.StatusCode -ne 200 -or $nginxResponse.Content -ne $staticContent) {
        throw "nginx static content validation failed"
    }

    $validationCode = @'
import json
from pathlib import Path
config = json.loads(Path("/home/node/.openclaw/openclaw.json").read_text())
assert config["gateway"]["mode"] == "local"
assert config["gateway"]["auth"]["mode"] == "token"
assert not config.get("agents", {}).get("list", [])
assert config["plugins"]["entries"]["openclaw-weixin"]["enabled"] is True
assert config["tools"]["agentToAgent"]["enabled"] is True
assert config["tools"]["agentToAgent"]["allow"] == []
assert config["tools"]["sessions"]["visibility"] == "all"
assert config["tools"]["web"]["fetch"]["enabled"] is True
assert config["tools"]["web"]["fetch"]["useTrustedEnvProxy"] is True
assert config["agents"]["defaults"]["subagents"]["allowAgents"] == ["*"]
assert config["update"]["checkOnStart"] is False
assert not config["gateway"]["auth"].get("token")
assert Path("/home/node/.openclaw/.unitag-seed-initialized").is_file()
assert not list(Path("/opt/unitag/openclaw-seed").glob("data/*"))
nginx_config = Path("/home/node/.openclaw/nginx/nginx.conf").read_text()
assert "listen __NGINX_PORT__ default_server;" in nginx_config
assert "root /home/node/.openclaw/workspace/public;" in nginx_config
assert "proxy_pass" not in nginx_config
assert Path("/tmp/nginx/nginx.pid").is_file()
'@.Replace("__NGINX_PORT__", $NginxPort.ToString())
    & docker exec $containerName python3 -c $validationCode
    if ($LASTEXITCODE -ne 0) { throw "Base seed/runtime configuration validation failed" }

    docker stop --time 10 $containerName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Container runtime did not stop after SIGTERM" }
    $exitCode = docker inspect --format '{{.State.ExitCode}}' $containerName
    if ($LASTEXITCODE -ne 0 -or $exitCode -ne "0") {
        docker logs --tail 100 $containerName
        throw "Container runtime did not exit cleanly after SIGTERM"
    }
    Write-Output "Validated base image: $ImageReference"
    Write-Output "ImageKind=agent-base LayoutProtocolVersion=$LayoutProtocolVersion GatewayHttp=200 NginxHttp=200 NginxPort=$NginxPort NoPrebuiltAgent=true RestrictedRuntime=true GracefulStop=true ExtensionsConfigured=true"
}
finally {
    if ($started) { docker rm --force $containerName | Out-Null }
}
