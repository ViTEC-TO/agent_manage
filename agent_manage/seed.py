from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


INITIALIZING_MARKER = ".unitag-seed-initializing"
INITIALIZED_MARKER = ".unitag-seed-initialized"
SECRET_FILE_NAMES = {"auth-profiles.json", "secrets.json"}
SECRET_KEY_NAMES = {"apikey", "password", "secret", "token"}


def assert_seed_has_no_runtime_secrets(seed_dir: Path) -> None:
    seed_dir = seed_dir.resolve()
    if not seed_dir.is_dir():
        raise FileNotFoundError(f"OpenClaw seed is missing: {seed_dir}")
    secret_files = [path for path in seed_dir.rglob("*") if path.name in SECRET_FILE_NAMES]
    if secret_files:
        raise ValueError(f"Secret-bearing file found in OpenClaw seed: {secret_files[0].name}")

    config_path = seed_dir / "openclaw.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"OpenClaw seed config is missing: {config_path}")
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    def visit(value: object, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = (*path, str(key))
                if str(key).lower() in SECRET_KEY_NAMES and item not in (None, "", [], {}):
                    raise ValueError(
                        f"Runtime secret found in OpenClaw seed config: {'.'.join(next_path)}"
                    )
                visit(item, next_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))

    visit(payload)


def initialize_openclaw_seed(seed_dir: Path, target_dir: Path) -> dict[str, object]:
    seed_dir = seed_dir.resolve()
    target_dir = target_dir.resolve()
    if not seed_dir.is_dir() or not any(seed_dir.iterdir()):
        raise FileNotFoundError(f"OpenClaw seed is missing or empty: {seed_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)
    initializing = target_dir / INITIALIZING_MARKER
    initialized = target_dir / INITIALIZED_MARKER
    existing = [item for item in target_dir.iterdir() if item.name != INITIALIZING_MARKER]
    if existing and not initializing.exists():
        return {
            "initialized": False,
            "reason": "target_not_empty",
            "target": str(target_dir),
        }

    initializing.touch(exist_ok=True)
    copied: list[str] = []
    try:
        for source in sorted(seed_dir.iterdir(), key=lambda item: item.name):
            destination = target_dir / source.name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)
            copied.append(source.name)
        initialized.write_text("1\n", encoding="ascii")
    finally:
        initializing.unlink(missing_ok=True)

    return {
        "initialized": True,
        "target": str(target_dir),
        "copied": copied,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="unitag-openclaw-seed")
    parser.add_argument("--seed", default="/opt/unitag/openclaw-seed")
    parser.add_argument("--target", default="/home/node/.openclaw")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        assert_seed_has_no_runtime_secrets(Path(args.seed))
        return 0
    initialize_openclaw_seed(Path(args.seed), Path(args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
