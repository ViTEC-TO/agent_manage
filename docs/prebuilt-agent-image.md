# Prebuilt Agent Image Workflow

## Purpose

Prebuilt images move `openclaw agents add` to image build time. Order startup only
runs `configure-instance`, which writes model and permission-ticket settings after
the writable OpenClaw volume is mounted.

## Required invariants

- The image is built for `linux/amd64` from an immutable OpenClaw base digest.
- `/opt/unitag/openclaw-seed` contains the registered template Agent and no runtime secrets.
- Seed config contains `gateway.mode=local` and `gateway.auth.mode=token`, but no token value.
- The entrypoint merges seed config as defaults and lets runtime config win recursively.
- Seed initialization preserves npm symbolic links required by installed plugins.
- `.unitag-seed-initialized` makes initialization idempotent; an initializing marker supports recovery.
- Gateway Token, model key, permission ticket, channel credentials, and auth profiles are runtime-only.
- `@larksuite/cli` and the Weixin, QQ Bot, and Feishu plugins are installed at image build time.
- CLI and plugin versions are pinned by Docker build arguments and recorded as image labels.
- OpenClaw-owned plugins are pinned to the base runtime version so their plugin API remains compatible.
- The seed enables Weixin, agent-to-agent access, all-session visibility, unrestricted subagents, and disables update checks.
- Layout protocol label `io.dola.unitag.layout-protocol-version=2` identifies this contract.

These rules address two real failure modes: skipping seed when DockerManager has
already created runtime configuration, and building a seed that cannot boot because
`gateway.mode` is absent.

## Build and validate

Docker must be running and the base image must be accessible.

```powershell
.\container-image\build-prebuilt-image.ps1 `
  -TemplateIdentify unipay-claw-base `
  -TemplateArchive C:\artifacts\unipay-claw-base.zip `
  -ImageTag sgccr.ccs.tencentyun.com/dola-agent/dola_claw-base:unipay-claw-base-0.1.3-amd64
```

The command does not merely inspect files. It starts a temporary container with a
non-empty tmpfs OpenClaw directory and a runtime-only test token, then requires:

- Gateway root returns HTTP 200.
- The requested prebuilt Agent exists.
- The runtime control-UI origin survives the seed merge.
- Gateway mode is `local` and auth mode is `token`.
- The runtime token is not persisted in `openclaw.json`.
- `@larksuite/cli` is globally installed and the required plugin/tool settings survive seed initialization.
- The initialized marker and image labels are correct.

The image is not pushed when validation fails.

## Publish and obtain the digest

Authenticate to the registry first, then run:

```powershell
.\container-image\publish-prebuilt-image.ps1 `
  -TemplateIdentify unipay-claw-base `
  -TemplateArchive C:\artifacts\unipay-claw-base.zip `
  -RegistryRepository sgccr.ccs.tencentyun.com/dola-agent/dola_claw-base `
  -Version 0.1.3
```

Use the printed `ImmutableReference`, not the mutable tag, as DockerManager's
`runtimeImageReference`.

## Runtime sequence

1. DockerManager creates an instance with the immutable image reference.
2. The container entrypoint initializes or reconciles the OpenClaw seed.
3. OpenClaw boots using the runtime `OPENCLAW_GATEWAY_TOKEN`.
4. DockerManager executes `configure-instance` with the model key on stdin.
5. DockerManager handles `restartRequired`, restores readiness, and publishes the Gateway route.

`configure-instance` reads Agent IDs and explicit workspace paths from the restored
`openclaw.json`. It does not inspect or extract the template archive or validate
prebuilt Agent registration. Legacy template arguments are accepted but ignored;
image build validation owns those checks.

## Troubleshooting

- Missing prebuilt Agent or workspace: verify layout protocol v2 and run the image validator; `configure-instance` does not recreate them.
- `existing config is missing gateway.mode`: the image predates the bootable seed contract; rebuild it.
- Gateway readiness timeout: inspect container logs before retrying; do not fall back silently to the base image.
- Lark CLI postinstall timeout: the Dockerfile uses a cacheable BuildKit download with a fixed SHA-256 and verifies the package-provided checksum again.
- Registry pull failure: verify the repository allowlist, registry credentials, and immutable digest.
- Never repair a failed image by adding secrets during build. Fix the seed contract and publish a new digest.
# Container nginx runtime

The prebuilt image installs nginx once during `docker build`; container startup never installs packages and does not require systemd. PID 1 remains the Unitag Python supervisor, which initializes the OpenClaw seed, validates nginx, starts nginx and OpenClaw, forwards `SIGTERM`/`SIGINT`, and terminates the peer process when either child exits.

- Persistent platform-managed config: `/home/node/.openclaw/nginx/nginx.conf`
- Public files only: `/home/node/.openclaw/workspace/public/`
- Ephemeral pid, logs, cache and temporary data: `/tmp/nginx/`
- Default container port: `80`
- Optional fallback: set `UNITAG_NGINX_PORT` on the first start of a fresh state volume, for example `8080`. The persisted config must continue to match that value on later starts.

The supervisor enforces the platform-owned shape of the configuration: one expected listen port, the declared public workspace root, runtime files under `/tmp/nginx/`, the existing single MIME include, disabled directory listing and disabled symlink serving. `alias` remains prohibited because it can bypass the public workspace root and expose persisted instance files. The supervisor does not maintain a network-directive blacklist or decide which `proxy_pass` targets are reachable. Nginx itself remains responsible for configuration syntax through `nginx -t`.

Network isolation is provided outside this parser by the Docker internal network, host `INPUT`/`FORWARD` firewall policy and the controlled Squid egress path. Custom locations, upstreams and proxy behavior remain an operator responsibility. The retained checks are a platform and file-delivery contract, not a claim that every possible nginx configuration is supported or safe to expose.

If the persistent nginx configuration fails the service policy or `nginx -t`, the supervisor logs the reason and starts nginx from the trusted image template rendered under `/tmp/nginx/`. It does not overwrite the rejected persistent file. The fallback is validated and tested independently; a broken image template remains a fatal startup error rather than being silently ignored.

DockerManager must continue to run the image as `node`, with all capabilities dropped and `no-new-privileges`; it must publish only the configured nginx port rather than arbitrary container ports.

This is a controlled serving policy, not a hard security boundary against hostile code running as the same Unix user. A same-UID OpenClaw tool can replace writable state or bind an exposed port. Hard tenant isolation still belongs at the container boundary: immutable image, fixed published ports, no Docker socket, no added capabilities, and host/network policy. Do not grant `NET_ADMIN`, `NET_BIND_SERVICE`, `privileged`, or a writable host nginx configuration mount.
