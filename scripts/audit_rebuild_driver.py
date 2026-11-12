"""Trusted, no-model dbt reconstruction inside a fresh Harbor task workspace.

Only declared submission files are installed. Initial image/data and package
policy belong to the caller's pinned task binding. This records execution; it
does not decide semantic correctness or retroactive candidate behavior.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def now():
    return datetime.now(timezone.utc).isoformat()


def hashed(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def local(root, name):
    root, name = Path(root).resolve(), Path(name)
    path = root / name
    if name.is_absolute() or not name.parts or '..' in name.parts or path.resolve() == root or not path.resolve().is_relative_to(root):
        raise ValueError('Unsafe declared project path: ' + str(name))
    if any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)):
        raise ValueError('Symlink in declared project path: ' + str(name))
    return path


def install_submission(spec, payload):
    project = Path(spec['project_root'])
    if not project.is_absolute() or project == Path('/') or project.is_symlink():
        raise ValueError('Unsafe project root')
    submission = spec['submission']
    destinations = [f['path'] for f in submission['files']]
    if len(destinations) != len(set(destinations)):
        raise ValueError('Duplicate submitted file')
    protected = [Path(p).resolve() for p in spec.get('protected_paths', [])]
    mutations = [local(project, p) for p in [*destinations, *submission.get('reset_paths', []),
                                            *submission.get('delete_paths', [])]]
    for path in mutations:
        if any(path.resolve() == p or p.is_relative_to(path.resolve()) or path.resolve().is_relative_to(p) for p in protected):
            raise ValueError('Mutation intersects protected data/dependencies: ' + str(path))
    # Verify every payload and destination before the first mutation.
    for item in submission['files']:
        source = local(payload, item['payload_path'])
        if hashed(source) != item['sha256']:
            raise ValueError('Submitted file hash changed: ' + item['path'])
    project.mkdir(parents=True, exist_ok=True)
    for name in [*submission.get('reset_paths', []), *submission.get('delete_paths', [])]:
        path = local(project, name)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    installed = []
    for item in submission['files']:
        target = local(project, item['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local(payload, item['payload_path']), target)
        installed.append({'path': str(target), 'sha256': hashed(target)})
    return installed


def capture_files(project, names, out):
    out.mkdir(parents=True, exist_ok=False)
    results = []
    for name in names:
        item = {'relative_path': name}
        try:
            source = local(project, name)
            item['exists'] = source.is_file()
            if source.is_file():
                if source.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError('Declared file exceeds 32 MiB capture limit')
                target = local(out, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                item.update(file=name, sha256=hashed(target), size=target.stat().st_size)
        except Exception as exc:
            item['error'] = str(exc)
        results.append(item)
    (out / 'inventory.json').write_text(json.dumps(results, indent=2) + '\n')
    return results


def capture_trees(project, names):
    result = []
    for name in names:
        base = local(project, name)
        item = {'relative_path': name, 'exists': base.is_dir(), 'files': []}
        if base.is_dir():
            for path in sorted(base.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Cannot certify symlinked dependency tree: ' + str(path))
                if path.is_file():
                    item['files'].append({'path': str(path.relative_to(base)), 'sha256': hashed(path)})
        item['content_sha256'] = hashlib.sha256(json.dumps(item['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        result.append(item)
    return result


def validate_commands(spec):
    commands = spec.get('commands')
    if not isinstance(commands, list):
        raise ValueError('Declare a list of dbt commands')
    for command in commands:
        if (not isinstance(command, list) or len(command) < 2
                or any(not isinstance(arg, str) for arg in command)
                or command[0] != 'dbt'
                or command[1] not in {'deps', 'seed', 'run', 'build', 'compile', 'test'}):
            raise ValueError('Only explicitly declared dbt commands are supported')


def validate_capture_hooks(spec):
    for hook in spec.get('capture_hooks', []):
        phase = hook.get('phase')
        if phase not in {'initial', 'final', 'after_command'}:
            raise ValueError('Unknown capture hook phase')
        if phase == 'after_command':
            index = hook.get('command_index')
            if type(index) is not int or not 0 <= index < len(spec['commands']):
                raise ValueError('Capture hook command index is outside the declared commands')
        elif 'command_index' in hook:
            raise ValueError('Command index requires after_command phase')


def capture_hooks(spec, payload, output, phase, env, command_index=None):
    runs = []
    for index, hook in enumerate(spec.get('capture_hooks', [])):
        if hook['phase'] != phase:
            continue
        if phase == 'after_command' and hook['command_index'] != command_index:
            continue
        script = local(payload, hook['payload_path'])
        if hashed(script) != hook['sha256']:
            raise ValueError('Capture hook hash changed')
        suffix = f'-command-{command_index:02d}' if phase == 'after_command' else ''
        stem = f'hook-{phase}-{index:02d}{suffix}'
        destination = output / stem
        args = [str(destination) if arg == '{output}' else arg for arg in hook['args']]
        command = [sys.executable, str(script), *args]
        run = {'command': command, 'phase': phase, 'started_at_utc': now(), 'script_sha256': hook['sha256']}
        if phase == 'after_command': run['command_index'] = command_index
        log = output / f'{stem}.log'
        with log.open('wb') as stream:
            try:
                proc = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                      timeout=spec.get('capture_timeout_sec', 120))
                run['returncode'] = proc.returncode
            except subprocess.TimeoutExpired:
                run.update(returncode=None, timeout=True)
        run.update(finished_at_utc=now(), log=log.name, log_sha256=hashed(log))
        runs.append(run)
    return runs


def execute(spec_path, output):
    spec_path, output = Path(spec_path), Path(output)
    spec = json.loads(spec_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'task_key': spec['task_key'], 'started_at_utc': now(),
              'driver_sha256': hashed(__file__), 'spec_sha256': hashed(spec_path),
              'model_requests': 0, 'runs': [], 'scope': 'Trusted reconstruction; no retrospective candidate chronology certification'}
    try:
        validate_commands(spec)
        validate_capture_hooks(spec)
        project = Path(spec['project_root'])
        env = os.environ.copy()
        allowed = {'DB_TYPE', 'DUCKDB_PATH', 'DBT_PROFILES_DIR', 'PYTHONDONTWRITEBYTECODE', 'DBT_SEND_ANONYMOUS_USAGE_STATS'}
        if set(spec.get('environment', {})) - allowed:
            raise ValueError('Undeclared environment override')
        env.update(spec.get('environment', {}))
        report['environment'] = {k: env.get(k) for k in sorted(allowed)}
        report['dbt_executable'] = shutil.which('dbt', path=env.get('PATH'))
        if report['dbt_executable']:
            report['dbt_executable_sha256'] = hashed(report['dbt_executable'])
        report['initial_input_hashes'] = [{'path': p, 'sha256': hashed(p)} for p in spec.get('input_files', [])]
        report['initial_trees'] = capture_trees(project, spec.get('capture_trees', []))
        report['initial_files'] = capture_files(project, spec['capture_files'], output / 'initial-files')
        report['initial_capture_hooks'] = capture_hooks(spec, spec_path.parent, output, 'initial', env)
        report['installed_files'] = install_submission(spec, spec_path.parent)
        report['submitted_files'] = capture_files(project, spec['capture_files'], output / 'submitted-files')
        # Remove candidate-controlled compiled artifacts before trusted dbt.
        for name in spec.get('fresh_artifact_paths', ['target', 'logs']):
            path = local(project, name)
            if any(path.resolve() == Path(p).resolve() or Path(p).resolve().is_relative_to(path.resolve()) or path.resolve().is_relative_to(Path(p).resolve()) for p in spec.get('protected_paths', [])):
                raise ValueError('Fresh artifact reset intersects protected data/dependencies')
            if path.is_dir(): shutil.rmtree(path)
            elif path.exists(): path.unlink()
        for index, command in enumerate(spec['commands']):
            run = {'command': command, 'cwd': str(project), 'started_at_utc': now()}
            with (output / f'command-{index:02d}.log').open('wb') as stream:
                try:
                    proc = subprocess.run(command, cwd=project, env=env, stdout=stream,
                                          stderr=subprocess.STDOUT, timeout=spec.get('timeout_sec', 1200))
                    run['returncode'] = proc.returncode
                except subprocess.TimeoutExpired:
                    run.update(returncode=None, timeout=True)
            run.update(finished_at_utc=now(), log=f'command-{index:02d}.log',
                       log_sha256=hashed(output / f'command-{index:02d}.log'))
            run['files'] = capture_files(project, spec['capture_files'], output / f'command-{index:02d}-files')
            run['capture_hooks'] = capture_hooks(spec, spec_path.parent, output, 'after_command', env, index)
            report['runs'].append(run)
            if run['returncode'] != 0:
                break
        report['build_success'] = len(report['runs']) == len(spec['commands']) and bool(report['runs']) and all(r['returncode'] == 0 for r in report['runs'])
        report['final_files'] = capture_files(project, spec['capture_files'], output / 'final-files')
        report['final_trees'] = capture_trees(project, spec.get('capture_trees', []))
        report['final_capture_hooks'] = capture_hooks(spec, spec_path.parent, output, 'final', env)
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        report['build_success'] = False
    report['finished_at_utc'] = now()
    (output / 'execution.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True); p.add_argument('--output', required=True)
    args = p.parse_args(); result = execute(args.spec, args.output)
    print(json.dumps({'build_success': result['build_success'], 'error': result.get('error')}))
