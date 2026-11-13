#!/usr/bin/env python3
"""Install a verified, complete NVM/Node/Qwen tree without package resolution.

This standalone stdlib script runs inside the task as the native agent user.
It never changes the task project, Python environment, or database.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import tarfile
import tempfile


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    root = Path(root)
    entries = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'Escaping installation symlink: {relative}')
            entries.append({'path': relative, 'kind':'symlink', 'target':target})
        elif path.is_file():
            entries.append({'path':relative, 'kind':'file', 'mode':mode,
                            'size':path.stat().st_size, 'sha256':sha256(path)})
        elif path.is_dir():
            entries.append({'path':relative, 'kind':'directory', 'mode':mode})
        else:
            raise ValueError(f'Unsupported installation entry: {relative}')
    return entries


def check_tree(root, expected):
    actual = inventory(root)
    if actual != expected:
        raise ValueError('Installation tree differs from the locked inventory')


def install(archive, inventory_path, target, archive_sha256, inventory_sha256):
    archive, inventory_path, target = map(Path, (archive, inventory_path, target))
    if sha256(archive) != archive_sha256 or sha256(inventory_path) != inventory_sha256:
        raise ValueError('Installation archive/inventory checksum mismatch')
    expected = json.loads(inventory_path.read_text())
    if target.is_symlink():
        raise ValueError('Refuse an existing symlink as NVM home')
    if target.exists():
        check_tree(target, expected)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    # Validate before publication. Some Apptainer writable overlays return
    # EXDEV even for directory rename within the same parent; only that error
    # permits a checked copy into a newly and exclusively created destination.
    with tempfile.TemporaryDirectory(prefix='.dbt-bench-qwen-', dir=target.parent) as tmp:
        staging = Path(tmp)
        with tarfile.open(archive, 'r:gz') as stream:
            members = stream.getmembers()
            for member in members:
                parts = Path(member.name).parts
                if not parts or parts[0] != 'nvm' or '..' in parts or Path(member.name).is_absolute():
                    raise ValueError('Invalid installation archive path')
                if not (member.isdir() or member.isfile() or member.issym()):
                    raise ValueError('Unsupported installation archive member')
            def safe_member(member, destination):
                filtered = tarfile.data_filter(member, destination)
                # data_filter otherwise drops directory modes, leaving them
                # dependent on the task user's umask. Preserve ordinary modes;
                # set-id/sticky bits are never restored.
                if filtered.isdir():
                    filtered.mode = member.mode & 0o777
                return filtered
            stream.extractall(staging, members=members, filter=safe_member)
        check_tree(staging/'nvm', expected)
        if target.exists() or target.is_symlink():
            raise ValueError('Installation destination appeared concurrently')
        try:
            os.rename(staging/'nvm', target)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            target.mkdir()  # Never merge into or clean another existing tree.
            try:
                shutil.copytree(staging/'nvm', target, symlinks=True, dirs_exist_ok=True)
                check_tree(target, expected)
            except BaseException:
                shutil.rmtree(target)
                raise
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--archive-sha256', required=True)
    p.add_argument('--inventory-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64','amd64'):
        raise ValueError('Locked installation is for Linux x86_64')
    changed = install(args.archive, args.inventory, Path.home()/'.nvm',
                      args.archive_sha256, args.inventory_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'installed':True, 'changed':changed,
        'archive_sha256':args.archive_sha256, 'inventory_sha256':args.inventory_sha256,
        'nvm_home':str(Path.home()/'.nvm'), 'uid':os.getuid(),
        'scope':'Verified fixed installation; runtime versions are checked by the native adapter'},indent=2)+'\n')


if __name__ == '__main__':
    main()
