"""Verify phase4 builds, including captured absolute targets for upstream projects."""
from pathlib import Path
import posixpath
from dbt_bench import io
from phase3_capture import Capture, timestamp
from review_tools import ROOT


def target_artifact_prefix(command, project_root):
    root = Path(project_root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Unsafe declared project root')
    project = Path(command[command.index('--project-dir') + 1]) if '--project-dir' in command else root
    if not project.is_absolute(): project = root / project
    target = Path(command[command.index('--target-path') + 1]) if '--target-path' in command else Path('target')
    if not target.is_absolute(): target = project / target
    target = Path(posixpath.normpath(str(target)))
    if not target.is_relative_to(root) or target == root:
        raise ValueError('dbt target is outside the declared capture root')
    return target.relative_to(root).as_posix()


def verify_trial(batch, trial, root=ROOT):
    """Verify actual process/source/data bindings; reward is reported separately."""
    batch, trial, root = map(Path, (batch, trial, root))
    cap = Capture(trial, root)
    manifest = cap.read(batch / 'manifest.json')
    cfg, result = cap.read(trial / 'config.json'), cap.read(trial / 'result.json')
    label = Path(cfg['task']['path']).name
    if label != Path(result['config']['task']['path']).name:
        raise ValueError('Result/config task mismatch')
    entry = manifest['tasks'][label]
    request_path = cap.checked(entry['request'])
    request = cap.read(request_path)
    base = trial / 'agent/audit-rebuild'
    execution = cap.read(base / 'execution.json')
    if not execution.get('build_success') or execution.get('error') or execution['model_requests'] != 0:
        raise ValueError('Reference did not actually build')
    if request['task_key'] != execution['task_key'] or request['task_key'] != entry['task_key']:
        raise ValueError('Task identity differs')
    cap.checked({'path': str(request_path), 'sha256': execution['spec_sha256']})
    cap.checked({'path': str(request_path.with_name('audit_rebuild_driver.py')), 'sha256': execution['driver_sha256']})
    for ref in [manifest['config'], manifest['requests'], *manifest['tools'], *entry['submission_source_refs']]:
        cap.checked(ref)
    if any(execution['environment'].get(k) != v for k, v in request['environment'].items()):
        raise ValueError('Actual build environment differs')
    submitted = cap.files(base / 'submitted-files', execution['submitted_files'])
    initial = cap.files(base / 'initial-files', execution['initial_files'])
    final = cap.files(base / 'final-files', execution['final_files'])
    for item in request['submission']['files']:
        name, digest = item['path'], item['sha256']
        cap.checked({'path': str(request_path.parent / item['payload_path']), 'sha256': digest})
        if io.file_hash(submitted[name]) != digest or io.file_hash(final[name]) != digest:
            raise ValueError('Submitted source differs from final source')
        if {'path': request['project_root'] + '/' + name, 'sha256': digest} not in execution['installed_files']:
            raise ValueError('Source installation not attested')
    commands = []
    if len(execution['runs']) != len(request['commands']) or not execution['runs']:
        raise ValueError('Declared build commands did not execute')
    for index, (run, command) in enumerate(zip(execution['runs'], request['commands'])):
        if run['command'] != command or run['returncode'] != 0 or run['cwd'] != request['project_root']:
            raise ValueError('Actual command differs or failed')
        cap.checked({'path': str(base / run['log']), 'sha256': run['log_sha256']})
        files = cap.files(base / f'command-{index:02d}-files', run['files'])
        if not any(action in command for action in ('run', 'build', 'test')):
            commands.append({'run': run, 'files': files, 'manifest': None,
                             'run_results': None, 'hooks': [cap.hook(h) for h in run.get('capture_hooks', [])]})
            continue
        target = target_artifact_prefix(command, request['project_root'])
        mp, rp = files[target + '/manifest.json'], files[target + '/run_results.json']
        m, rr = cap.read(mp), cap.read(rp)
        if m['metadata']['invocation_id'] != rr['metadata']['invocation_id']:
            raise ValueError('Manifest/run_results mismatch')
        for artifact in (m, rr):
            if not timestamp(run['started_at_utc']) - 1 <= timestamp(artifact['metadata']['generated_at']) <= timestamp(run['finished_at_utc']) + 1:
                raise ValueError('Stale dbt artifact')
        commands.append({'run': run, 'files': files, 'manifest': m, 'run_results': rr,
                         'manifest_path': mp, 'results_path': rp,
                         'hooks': [cap.hook(h, allow_test_manifest_without_compiled='test' in command)
                                   for h in run.get('capture_hooks', [])]})
    if not any(c['manifest'] is not None and any(x in c['run']['command'] for x in ('run', 'build')) for c in commands):
        raise ValueError('No materialization command actually ran')
    if {k: io.file_hash(p) for k, p in commands[-1]['files'].items()} != {k: io.file_hash(p) for k, p in final.items()}:
        raise ValueError('Final files changed after last observed command')
    first_hooks = [cap.hook(h) for h in execution['initial_capture_hooks']]
    last_hooks = [cap.hook(h, allow_test_manifest_without_compiled='test' in execution['runs'][-1]['command'])
                  for h in execution['final_capture_hooks']]
    if len(first_hooks) != 1 or len(last_hooks) != 1:
        raise ValueError('Expected one complete raw projection per initial/final capture')
    a, z = first_hooks[0], last_hooks[0]
    for key in a['raw']:
        before, after = a['raw'][key], z['raw'][key]
        if (before['columns'], before['rows'], before['sql_schema']) != (after['columns'], after['rows'], after['sql_schema']):
            raise ValueError('Raw input schema/size changed')
        if sorted(cap.runtime(before).read_bytes().splitlines()) != sorted(cap.runtime(after).read_bytes().splitlines()):
            raise ValueError('Raw input values changed')
    echo_path = trial / 'agent/audit-backend-before-submission-shell.json'
    echo = cap.read(echo_path)
    if not timestamp(echo['finished_at_utc']) < timestamp(execution['initial_capture_hooks'][0]['finished_at_utc']) < timestamp(execution['runs'][0]['started_at_utc']):
        raise ValueError('Backend/source/build chronology differs')
    if cap.read(trial / 'agent/no-model.json')['model_requests'] != 0:
        raise ValueError('Unexpected model request')
    return {'capture': cap, 'manifest': manifest, 'entry': entry, 'request': request,
            'execution': execution, 'result': result, 'commands': commands,
            'initial_files': initial, 'final_files': final, 'initial': a, 'final': z,
            'backend_shell': echo, 'backend_shell_path': echo_path, 'label': label}
