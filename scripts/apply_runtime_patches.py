#!/usr/bin/env python3
"""Apply only source-lock-listed, exact-hash Harbor compatibility patches."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import stat
import tempfile

from dbt_bench.io import contained, evidence_ok, evidence_ref, file_hash, read_json, write_json


def resolve_regression(ref, root):
    """Allow a byte-identical committed mirror when a large-run path is absent.

    A present but altered original must fail rather than hiding tampering behind
    a mirror. The lock's content hash, not a replacement result, is authoritative.
    """
    original = contained(root, ref['path'])
    if original.exists():
        if not evidence_ok(ref, root):
            raise ValueError('Runtime patch regression evidence changed')
        return ref
    checksum = ref.get('sha256', '')
    if not re.fullmatch('[0-9a-f]{64}', checksum):
        raise ValueError('Invalid runtime regression checksum')
    mirror = Path(root) / 'validation/evidence/runtime-patch-assets' / checksum
    if not mirror.is_file() or file_hash(mirror) != checksum:
        raise ValueError('Runtime patch regression evidence missing or changed')
    return evidence_ref(mirror, root)


def plan_updates(edits_by_path):
    """Resolve ordered exact-hash patch chains before changing any installed file."""
    staged = []
    for target, edits in edits_by_path.items():
        hashes = [edits[0]['before_sha256']]
        for edit in edits:
            if hashes[-1] != edit['before_sha256']:
                raise ValueError(f'Non-contiguous runtime patch chain: {target}')
            hashes.append(edit['after_sha256'])
        if len(hashes) != len(set(hashes)):
            raise ValueError(f'Ambiguous runtime patch chain: {target}')
        actual = file_hash(target)
        if actual not in hashes:
            raise ValueError(f'Runtime patch preimage mismatch: {target}')
        offset = hashes.index(actual)
        if offset == len(edits):
            continue
        after = target.read_text()
        for edit in edits[offset:]:
            replacements = edit.get('replacements', [edit])
            for replacement in replacements:
                if after.count(replacement['before_text']) != 1:
                    raise ValueError('Runtime patch context is not unique')
                after = after.replace(replacement['before_text'], replacement['after_text'], 1)
            if hashlib.sha256(after.encode()).hexdigest() != edit['after_sha256']:
                raise ValueError('Runtime patch postimage mismatch')
        staged.append((target, actual, after))
    return staged


def replace_installed_file(target, text):
    """Detach uv/pip hardlinks; never write patches through a shared cache inode."""
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, temporary = tempfile.mkstemp(prefix='.dbt-bench-patch-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(root=ROOT):
    lock = read_json(root / "sources.lock.yaml")
    package = Path(importlib.util.find_spec("harbor").origin).parent
    edits_by_path, records = {}, []
    for entry in lock.get("runtime_patches", []):
        meta_path = contained(root, entry["metadata"])
        if file_hash(meta_path) != entry["sha256"]:
            raise ValueError("Runtime patch metadata changed")
        meta = read_json(meta_path)
        if importlib.metadata.version(meta["package"]) != meta["package_version"]:
            raise ValueError("Runtime patch package version mismatch")
        if not meta["regression_evidence"]:
            raise ValueError("Runtime patch regression evidence missing")
        resolved = [resolve_regression(r, root) for r in meta['regression_evidence']]
        for edit in meta["files"]:
            target = contained(package, edit["path"])
            edits_by_path.setdefault(target, []).append(edit)
        records.append({"id": meta["id"], "metadata": evidence_ref(meta_path, root),
                        "regression_evidence_resolved": resolved})
    staged = plan_updates(edits_by_path)
    for target, original_hash, text in staged:
        if file_hash(target) != original_hash:
            raise ValueError('Installed runtime changed after patch planning')
        backup = root / ".cache/runtime-originals" / (original_hash + ".py")
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists() and file_hash(backup) != original_hash:
            raise ValueError("Runtime original backup corrupted")
        if not backup.exists():
            shutil.copyfile(target, backup)
        replace_installed_file(target, text)
    result = {"package_version": importlib.metadata.version("harbor"), "patches": records,
              "files_changed": len(staged), "source_lock": evidence_ref(root / "sources.lock.yaml", root)}
    write_json(root / "artifacts/runtime/patches-applied.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    apply()
