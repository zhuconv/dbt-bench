"""Read-only capture v2: distinguish observed absence from capture failures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import stat
from datetime import datetime, timezone


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def path_fact(path):
    """ENOENT is an observation; permission/IO errors are not absence."""
    try:
        item = Path(path).lstat()
        if stat.S_ISLNK(item.st_mode):
            raise ValueError('Declared root/database symlink needs explicit capture support: ' + str(path))
        return {'path': str(path), 'exists': True,
                'kind': 'directory' if stat.S_ISDIR(item.st_mode) else
                        'regular_file' if stat.S_ISREG(item.st_mode) else 'other'}
    except FileNotFoundError:
        return {'path': str(path), 'exists': False, 'kind': 'absent', 'errno': 2}


def collect(spec, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    project = Path(spec['project_root'])
    result = {'schema_version': 2, 'task_key': spec['task_key'],
              'stage': 'agent-submission-before-native',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'project_root': str(project), 'databases': [], 'project_files': [],
              'errors': [], 'absence_observations': [], 'training_prohibited': True}
    observed_absent = []
    try:
        result['project_observation'] = path_fact(project)
        if not result['project_observation']['exists']:
            observed_absent.append(('project_root', project))
        elif result['project_observation']['kind'] != 'directory':
            result['errors'].append('Project path is not a directory')
    except (OSError, ValueError) as exc:
        result['project_observation'] = {'path': str(project), 'exists': None, 'error': str(exc)}
        result['errors'].append('Project stat failed: ' + str(exc))
    # Retain exactly declared DBs and their WALs; never connect or checkpoint.
    for index, name in enumerate(spec['database_paths']):
        for suffix in ('', '.wal'):
            source = Path(name + suffix)
            try:
                entry = path_fact(source)
            except (OSError, ValueError) as exc:
                result['errors'].append('Database stat failed: ' + str(exc))
                result['databases'].append({'path': str(source), 'exists': None, 'error': str(exc)})
                continue
            if not entry['exists']:
                observed_absent.append(('database_wal' if suffix else 'database', source))
                result['databases'].append(entry)
                continue
            if entry['kind'] != 'regular_file':
                result['errors'].append('Database path is not a regular file: ' + str(source))
                result['databases'].append(entry)
                continue
            destination = output / 'databases' / (str(index) + '.duckdb' + suffix)
            destination.parent.mkdir(exist_ok=True)
            try:
                before = sha(source)
                shutil.copyfile(source, destination)
                after = sha(source)
                copied = sha(destination)
            except OSError as exc:
                entry['error'] = str(exc)
                result['errors'].append('Database capture failed: ' + str(source) + ': ' + str(exc))
                result['databases'].append(entry)
                continue
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
    def walk_error(exc):
        result['errors'].append('Project inventory failed: ' + str(exc))
    with tarfile.open(archive, 'w:gz') as tar:
        for current, dirs, files in os.walk(project, followlinks=False, onerror=walk_error) if result['project_observation'].get('exists') is True else []:
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
    # Recheck every absence at the end; disappearance/appearance during capture
    # is not a complete inventory. Missing optional WAL is not a task failure.
    for role, path in observed_absent:
        try:
            final = path_fact(path)
            absent = final['exists'] is False
            result['absence_observations'].append({'role': role, 'path': str(path),
                'before': {'exists': False, 'errno': 2}, 'after': final,
                'stable_absence': absent})
            if not absent:
                result['errors'].append('Previously absent path appeared during capture: ' + str(path))
        except (OSError, ValueError) as exc:
            result['errors'].append('Absence recheck failed: ' + str(exc))
    result.update(project_archive={'file': archive.name, 'sha256': sha(archive),
                                  'uncompressed_bytes': total},
                  inventory_complete=not result['errors'],
                  inventory_policy={'suffixes': sorted(suffixes), 'excluded_directory_names': sorted(excluded),
                                    'per_file_limit_bytes': 128 * 1024 * 1024,
                                    'total_limit_bytes': 512 * 1024 * 1024},
                  complete=not result['errors'], finished_at=datetime.now(timezone.utc).isoformat(),
                  scope='Complete bounded inventory may prove absence; this collector assigns no score. IO/access/symlink/race failures remain incomplete. No whole /app copy, SQL, model request or native reward modification.')
    (output / 'capture.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec-json', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = collect(json.loads(a.spec_json), a.output)
    print(json.dumps({'complete': result['complete'], 'errors': result['errors']}))
