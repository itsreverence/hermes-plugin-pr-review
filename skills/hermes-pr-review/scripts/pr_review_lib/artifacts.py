"""Private artifact primitives adapted from the legacy review writer."""
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def reject_symlinks(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("managed path contains a symlink")
    return path


def private_dir(path):
    path = reject_symlinks(path)
    if not path.exists():
        private_dir(path.parent)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if not path.is_dir():
        raise ValueError("managed path is not a directory")
    return path


def managed_root(path):
    path = private_dir(path)
    mode = path.stat()
    if mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) & 0o077:
        raise ValueError("state root must be owned by current user and mode 0700")
    return path


def private_file(path):
    path = reject_symlinks(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("managed file must be private, regular, and not hard-linked")
    finally:
        os.close(fd)


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def write_text(path, text):
    path = reject_symlinks(path)
    managed_root(private_dir(path.parent))
    if path.exists():
        raise ValueError("immutable artifact already exists")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Exclusive link prevents overwriting an existing evidence file.
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_text(path):
    path = reject_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4_000_000:
            raise ValueError("invalid or oversized artifact")
        text = handle.read(4_000_001)
        if len(text) > 4_000_000:
            raise ValueError("oversized artifact")
        return text


def parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("nonfinite JSON number")
    return json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)


def read_json(path):
    return parse_json(read_text(path))
