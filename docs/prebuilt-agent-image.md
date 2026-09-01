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
- `.unitag-seed-initialized` makes initialization idempotent; an initializing marker supports recovery.
- Gateway Token, model key, permission ticket, channel credentials, and auth profiles are runtime-only.
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

## Troubleshooting

- `Prebuilt agent is missing`: verify layout protocol v2 and run the validator.
- `existing config is missing gateway.mode`: the image predates the bootable seed contract; rebuild it.
- Gateway readiness timeout: inspect container logs before retrying; do not fall back silently to the base image.
- Registry pull failure: verify the repository allowlist, registry credentials, and immutable digest.
- Never repair a failed image by adding secrets during build. Fix the seed contract and publish a new digest.
