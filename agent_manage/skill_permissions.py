"""Keep installed shared Skills writable by their environment owner."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def prepare_shared_skill_path(path: Path, environment: Path, *, recursive: bool = True) -> None:
    """Preserve executable/group bits; never follow links or change the environment."""
    if path.is_symlink():
        raise ValueError(f"Shared Skill root must not be a link: {path}")
    if not path.exists():
        return
    owner = environment.stat()

    def prepare(item: Path) -> None:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            return
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f"Shared Skill special file is not supported: {item}")
        mode = stat.S_IMODE(info.st_mode)
        required = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
        if info.st_uid != owner.st_uid:
            if os.geteuid() != 0:
                raise PermissionError(f"Shared Skill ownership mismatch: {item}; "
                                      f"expected environment uid {owner.st_uid}, found {info.st_uid}")
            os.chown(item, owner.st_uid, owner.st_gid, follow_symlinks=False)
        if mode | required != mode:
            item.chmod(mode | required)

    prepare(path)
    if recursive and path.is_dir():
        def fail(error: OSError) -> None:
            raise error
        for root, dirs, files in os.walk(path, followlinks=False, onerror=fail):
            for name in dirs + files:
                prepare(Path(root) / name)
