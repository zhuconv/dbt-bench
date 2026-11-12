"""Deterministic identities and local evidence references (no implicit network reads)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_manifest(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    result = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"Symlink escapes task: {path}")
            result.append({"path": rel, "symlink": os.readlink(path)})
        elif path.is_file():
            result.append({"path": rel, "sha256": file_hash(path),
                           "executable": bool(path.stat().st_mode & 0o111)})
    return result


def tree_hash(root):
    return digest(tree_manifest(root))


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        f.write(text)
        tmp = Path(f.name)
    tmp.replace(path)


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                                allow_nan=False) + "\n")


def write_jsonl(path, values):
    write_text(path, "".join(canonical(v).decode() + "\n" for v in values))


def unique_index(rows, key):
    result = {}
    for row in rows:
        value = row[key]
        if value in result:
            raise ValueError(f"Duplicate {key}: {value}")
        result[value] = row
    return result


def contained(root, relative):
    path = Path(root) / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError(f"Path escapes root: {relative}")
    return path


def evidence_ref(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    return {"path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
            "sha256": file_hash(path)}


def evidence_ok(ref, root):
    if not isinstance(ref, dict) or not ref.get("path") or not ref.get("sha256"):
        return False
    path = Path(root) / ref["path"]
    return path.is_file() and file_hash(path) == ref["sha256"]

