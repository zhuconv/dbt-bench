"""Read-only pre-verifier evidence collection; contains no checker or reference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
from datetime import datetime, timezone


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect(spec, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    project = Path(spec['project_root'])
    result = {'schema_version': 1, 'task_key': spec['task_key'],
              'stage': 'agent-submission-before-native',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'project_root': str(project), 'databases': [], 'project_files': [],
              'errors': [], 'training_prohibited': True}
    # Retain exactly declared DBs and their WALs; never connect or checkpoint.
    for index, name in enumerate(spec['database_paths']):
        for suffix in ('', '.wal'):
            source = Path(name + suffix)
            entry = {'path': str(source), 'exists': source.is_file()}
            if not entry['exists']:
                if not suffix:
                    result['errors'].append('Missing declared database: ' + str(source))
                result['databases'].append(entry)
                continue
            destination = output / 'databases' / (str(index) + '.duckdb' + suffix)
            destination.parent.mkdir(exist_ok=True)
            before = sha(source)
            shutil.copyfile(source, destination)
            after = sha(source)
            copied = sha(destination)
            entry.update(file=str(destination.relative_to(output)), size=destination.stat().st_size,
                         sha256=copied, source_before=before, source_after=after,
                         unchanged=before == copied == after)
            if not entry['unchanged']:
                result['errors'].append('Database changed during capture: ' + str(source))
            result['databases'].append(entry)
    suffixes = {'.sql', '.py', '.yml', '.yaml', '.json', '.toml', '.txt', '.md', '.csv'}
    excluded = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.cache'}
    archive = output / 'project.tar.gz'
    total = 0
    with tarfile.open(archive, 'w:gz') as tar:
        for current, dirs, files in os.walk(project, followlinks=False):
            for name in list(dirs):
                child = Path(current) / name
                if name in excluded:
                    dirs.remove(name)
                elif child.is_symlink():
                    result['errors'].append('Uncaptured directory symlink: ' + str(child))
                    dirs.remove(name)
            dirs.sort()
            for name in sorted(files):
                source = Path(current) / name
                if source.suffix.lower() not in suffixes:
                    continue
                relative = source.relative_to(project).as_posix()
                if not source.resolve().is_relative_to(project.resolve()):
                    result['errors'].append('Out-of-project source symlink: ' + relative)
                    continue
                try:
                    size = source.stat().st_size
                    if size > 128 * 1024 * 1024 or total + size > 512 * 1024 * 1024:
                        result['errors'].append('Explicit source capture size limit: ' + relative)
                        continue
                    before = sha(source)
                    info = tar.gettarinfo(str(source.resolve()), arcname=relative)
                    with source.open('rb') as stream:
                        tar.addfile(info, stream)
                    after = sha(source)
                    total += size
                    result['project_files'].append({'path': relative, 'size': size,
                                                    'sha256': before, 'unchanged': before == after})
                    if before != after:
                        result['errors'].append('Source changed during capture: ' + relative)
                except OSError as exc:
                    result['errors'].append(relative + ': ' + str(exc))
    if not project.is_dir() or not result['project_files']:
        result['errors'].append('Declared project absent or no source files captured')
    result.update(project_archive={'file': archive.name, 'sha256': sha(archive),
                                  'uncompressed_bytes': total},
                  complete=not result['errors'], finished_at=datetime.now(timezone.utc).isoformat(),
                  scope='Declared DB bytes/WAL and bounded project source/metadata only; no whole /app copy, model request, SQL execution or native reward modification.')
    (output / 'capture.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec-json', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = collect(json.loads(a.spec_json), a.output)
    print(json.dumps({'complete': result['complete'], 'errors': result['errors']}))
