"""Run prepared native Harbor jobs sequentially with one locked model service.

This is a finite launch wrapper, not a scheduler: no retries, new attempts,
budget overrides, synthetic requests, or automatic continuation allocations.
"""
from dbt_bench.local_config import expand as _release_expand
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from review_tools import cached_hashes
from run_difficulty_gpu_v2 import validate as validate_native_member
from run_gh_smoke import inference_environment, stop
PURPOSE = 'difficulty_only_no_training'
SLURM_SCRIPT = Path('scripts/slurm/gh-difficulty-batch.sbatch')

class ServiceDied(RuntimeError):
    pass

class BatchInterrupted(RuntimeError):
    pass

def scratch(path):
    path = Path(path).resolve()
    if not path.is_relative_to('/scratch'):
        raise ValueError('Execution and logs require a prepared SCRATCH directory')
    return path

def unchanged(ref, root):
    if not io.evidence_ok(ref, root):
        raise ValueError('Prepared evidence changed: ' + str(ref.get('path')))

def preflight(directory, root):
    evidence = io.read_json(directory / 'preflight.json')
    expected = {'runner': Path(__file__), 'slurm_script': root / SLURM_SCRIPT, 'manifest': directory / 'manifest.json'}
    for name, path in expected.items():
        unchanged(evidence[name], root)
        if (root / evidence[name]['path']).resolve() != path.resolve():
            raise ValueError('Preflight binds a different ' + name)
    dependencies = evidence.get('execution_dependencies', [])
    for ref in dependencies:
        unchanged(ref, root)
    paths = {(root / ref['path']).resolve() for ref in dependencies}
    required = {root / 'scripts' / name for name in ('run_difficulty_gpu_v2.py', 'run_gh_smoke.py', 'review_tools.py')}
    if not {path.resolve() for path in required} <= paths:
        raise ValueError('Preflight lacks imported execution dependency bindings')

def unlaunched(directory):
    for name in ('launch.json', 'launch-result.json', 'runtime-status.json', 'jobs'):
        if (directory / name).exists():
            raise ValueError('Existing execution must be preserved, never restarted: ' + str(directory / name))

def validate_member(entry, batch, identity, root):
    directory = scratch(entry['run'])
    if not Path(entry['run']).is_absolute():
        raise ValueError('Member run must be absolute')
    unchanged(entry['manifest'], root)
    if (root / entry['manifest']['path']).resolve() != directory / 'manifest.json':
        raise ValueError('Member manifest points outside its run')
    unlaunched(directory)
    manifest, config, rules = validate_native_member(directory, root)
    member = manifest['identity']
    if member.get('shared_model_batch') != str(batch) or member.get('server_seed') != identity['server_seed'] or member.get('measurement_rules') != identity['measurement_rules'] or (member.get('scheduling_amendment') != identity['scheduling_amendment']) or (member['attempt_ids'] != [1, 2, 3, 4]) or (config['n_attempts'] != 4):
        raise ValueError('Member differs from the shared service / four-attempt protocol')
    expected = [{'task_key': member['task_key'], 'attempt': n} for n in (1, 2, 3, 4)]
    if manifest['planned'] != expected:
        raise ValueError('Member planned slots differ from its actual configuration')
    preflight(directory, root)
    return (manifest, config, rules)

def validate_batch(directory, root=ROOT):
    directory, root = (scratch(directory), Path(root))
    unlaunched(directory)
    manifest = io.read_json(directory / 'manifest.json')
    identity = manifest['identity']
    if manifest.get('schema_version') != 1 or manifest.get('training_prohibited') is not True or manifest.get('purpose') != PURPOSE or (io.digest(identity) != manifest['experiment_id']) or (identity.get('walltime_sec') != 172800) or (type(identity.get('server_seed')) is not int):
        raise ValueError('Batch identity / authorization differs')
    for field in ('measurement_rules', 'scheduling_amendment'):
        unchanged(identity[field], root)
    amendment = io.read_json(root / identity['scheduling_amendment']['path'])
    if amendment.get('account') != _release_expand('${ALLOCATION}') or amendment.get('partition') != 'gh' or amendment.get('walltime_sec') != 172800 or (amendment.get('training_prohibited') is not True):
        raise ValueError('Scheduling amendment is not the authorized gh allocation')
    if identity.get('plan'):
        unchanged(identity['plan'], root)
    if not identity['members']:
        raise ValueError('Empty batch')
    preflight(directory, root)
    planned, directories = ([], set())
    for entry in identity['members']:
        run = str(Path(entry['run']).resolve())
        if run in directories:
            raise ValueError('Duplicate batch member')
        directories.add(run)
        member, _, _ = validate_member(entry, directory, identity, root)
        planned.extend(member['planned'])
    slots = [(row['task_key'], row['attempt']) for row in planned]
    if planned != identity['planned'] or len(slots) != len(set(slots)):
        raise ValueError('Duplicate, missing or reordered planned slots')
    return manifest

def allocation(job):
    raw = subprocess.run(['scontrol', 'show', 'job', '-o', job], capture_output=True, text=True, check=True).stdout
    fields = dict((part.split('=', 1) for part in raw.split() if '=' in part))
    if fields.get('Account') != _release_expand('${ALLOCATION}') or fields.get('Partition') != 'gh' or fields.get('TimeLimit') not in ('2-00:00:00', '48:00:00') or (fields.get('NumNodes') != '1') or (fields.get('NumTasks') != '1'):
        raise ValueError(_release_expand('Require ${ALLOCATION} / gh / 48 hours / one node / one task'))
    return raw

def environment(directory, job, root):
    env = {**os.environ, 'OPENAI_API_KEY': 'local-no-auth', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(root / 'src') + os.pathsep + str(root / 'scripts'), 'APPTAINER_TMPDIR': '/tmp/dbt-difficulty-' + job, 'OMP_NUM_THREADS': '8'}
    env.pop('LD_PRELOAD', None)
    env['PATH'] = str(root / 'runtime/.venv/bin') + ':/opt/apps/tacc-apptainer/1.4.1/bin:' + env.get('PATH', '')
    Path(env['APPTAINER_TMPDIR']).mkdir(exist_ok=True)
    return env

def model_environment(directory, env, root):
    result = inference_environment(root, env)
    result.update(VLLM_CACHE_ROOT=str(directory / 'cache/vllm'), TRITON_CACHE_DIR=str(directory / 'cache/triton'), FLASHINFER_WORKSPACE_BASE=str(directory / 'cache/flashinfer'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    for name in ('VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR', 'FLASHINFER_WORKSPACE_BASE'):
        Path(result[name]).mkdir(parents=True, exist_ok=True)
    return result

def model_command(directory, manifest, root):
    spec = io.read_json(root / 'inference/qwen3.6-35b-a3b.json')
    model = io.read_json(root / 'artifacts/inference/model.json')
    if not model['verified'] or model['revision'] != spec['revision'] or model['spec_sha256'] != io.file_hash(root / 'inference/qwen3.6-35b-a3b.json'):
        raise ValueError('Locked model inventory differs')
    snapshot = Path(model['snapshot'])
    for item in model['files']:
        if (snapshot / item['path']).stat().st_size != item['size']:
            raise ValueError('Locked model cache changed')
    command = [str(root / 'inference/.venv/bin/vllm'), 'serve', str(snapshot), '--served-model-name', spec['served_model_name'], *spec['server_args'], '--override-generation-config', json.dumps(spec['generation_defaults']), '--seed', str(manifest['identity']['server_seed'])]
    io.write_json(directory / 'server-command.json', {'argv': command, 'model': io.evidence_ref(root / 'artifacts/inference/model.json', root), 'runtime_lock': io.evidence_ref(root / 'inference/uv.lock', root), 'runtime_environment': io.evidence_ref(root / 'inference/runtime-env.json', root), 'shared_model_service': True, 'model_requests_outside_measurement': 0, 'training_prohibited': True, 'model_cache_access': 'Existing local snapshot; offline resolution, no download.'})
    return (command, spec['served_model_name'])

def alive(server):
    code = server.poll()
    if code is not None:
        raise ServiceDied('Shared pinned model service exited: ' + str(code))

def wait_ready(server, model, directory):
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        alive(server)
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=5) as response:
                models = json.load(response)
            if model not in [entry['id'] for entry in models['data']]:
                raise ValueError('Unexpected endpoint model')
            io.write_json(directory / 'models.json', models)
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(3)
    raise TimeoutError('Shared model startup exceeded 20 minutes')

def claim(directory, state):
    with (directory / 'launch.json').open('x') as handle:
        json.dump(state, handle, sort_keys=True, indent=2)
        handle.write('\n')

def state_for(directory, manifest, job, batch, root):
    shutil.copyfile(__file__, directory / 'runner.py')
    return {'schema_version': 1, 'slurm_job_id': job, 'hostname': socket.gethostname(), 'started_at': time.time(), 'experiment_id': manifest['experiment_id'], 'runner': io.evidence_ref(directory / 'runner.py', root), 'helper': io.evidence_ref(root / 'scripts/run_gh_smoke.py', root), 'planned': manifest.get('planned', manifest['identity'].get('planned')), 'training_prohibited': True, 'protocol_model_requests': 0, 'complete': False, 'shared_model_service': True, 'shared_model_batch': str(batch), 'batch_manifest': io.evidence_ref(batch / 'manifest.json', root), 'server_seed': manifest['identity']['server_seed']}

def terminal_trials(directory, config, state, root):
    paths = sorted((directory / 'jobs' / config['job_name']).glob('*/result.json'))
    state['raw_trials'] = [io.evidence_ref(path, root) for path in paths]
    records, errors = ([], [])
    for path in paths:
        try:
            record = io.read_json(path)
            if not record.get('id') or not record.get('trial_name') or (not record.get('finished_at')):
                errors.append({'path': str(path), 'reason': 'Not a complete terminal native trial'})
            else:
                records.append(record)
        except (ValueError, OSError) as exc:
            errors.append({'path': str(path), 'reason': str(exc)})
    if len({r['id'] for r in records}) != len(records):
        errors.append({'reason': 'Duplicate native trial ID'})
    state['native_terminal_check'] = {'expected': 4, 'actual_files': len(paths), 'terminal_trials': len(records), 'errors': errors, 'passed': len(paths) == 4 and len(records) == 4 and (not errors)}
    state['complete'] = state['native_terminal_check']['passed']
    state['audit_status'] = 'pending_posthoc_submission_audit'

def cleanup(directory, env, state, root):
    try:
        result = subprocess.run([str(root / 'runtime/.venv/bin/python'), str(root / 'scripts/verify_container_cleanup.py'), '--jobs', str(directory / 'jobs'), '--output', str(directory / 'cleanup.json')], cwd=root, env=env, capture_output=True, text=True, timeout=120)
        (directory / 'cleanup.log').write_text(result.stdout + result.stderr)
        state['cleanup_returncode'] = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        state['cleanup_error'] = {'type': type(exc).__name__, 'message': str(exc)}

def run_member(entry, batch, batch_manifest, server, env, job, root):
    directory = Path(entry['run'])
    manifest, config, _ = validate_member(entry, batch, batch_manifest['identity'], root)
    alive(server)
    state = state_for(directory, manifest, job, batch, root)
    state.update(server_pid=server.pid, server_ready_at=io.read_json(batch / 'runtime-status.json')['server_ready_at'])
    claim(directory, state)
    harbor = None
    try:
        with (directory / 'harbor.log').open('w') as handle:
            harbor = subprocess.Popen([str(root / 'runtime/.venv/bin/harbor'), 'run', '-c', str(directory / 'config.yaml')], cwd=root, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            state.update(harbor_pid=harbor.pid, harbor_started_at=time.time())
            io.write_json(directory / 'runtime-status.json', state)
            while harbor.poll() is None:
                alive(server)
                time.sleep(1)
            state['harbor_returncode'] = harbor.returncode
            alive(server)
    except BaseException as exc:
        state['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        if isinstance(exc, (ServiceDied, BatchInterrupted, KeyboardInterrupt, SystemExit)):
            raise
    finally:
        stop(harbor)
        terminal_trials(directory, config, state, root)
        cleanup(directory, env, state, root)
        state['finished_at'] = time.time()
        io.write_json(directory / 'launch-result.json', state)
    return state

def run_batch(directory, root=ROOT):
    directory, root = (scratch(directory), Path(root))
    job = os.environ.get('SLURM_JOB_ID')
    if not job:
        raise ValueError('An actual Slurm allocation is required')
    with cached_hashes():
        manifest = validate_batch(directory, root)
        allocation_text = allocation(job)
        state = state_for(directory, manifest, job, directory, root)
        claim(directory, state)
        (directory / 'allocation.txt').write_text(allocation_text)
        state['members'] = []
        server = None
        previous_handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}

        def interrupted(number, frame):
            raise BatchInterrupted('Allocation/process signal ' + str(number))
        for number in previous_handlers:
            signal.signal(number, interrupted)
        try:
            env = environment(directory, job, root)
            with (directory / 'gpu.txt').open('w') as handle:
                subprocess.run(['nvidia-smi', '-q'], stdout=handle, stderr=subprocess.STDOUT, check=True, timeout=30)
            command, model = model_command(directory, manifest, root)
            server_env = model_environment(directory, env, root)
            io.write_json(directory / 'cache-paths.json', {key: server_env[key] for key in ('VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR', 'FLASHINFER_WORKSPACE_BASE')})
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 8000))
            with (directory / 'vllm.log').open('w') as handle:
                server = subprocess.Popen(command, cwd=root, env=server_env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
                state['server_pid'] = server.pid
                wait_ready(server, model, directory)
                state['server_ready_at'] = time.time()
                io.write_json(directory / 'runtime-status.json', state)
                for entry in manifest['identity']['members']:
                    member = run_member(entry, directory, manifest, server, env, job, root)
                    state['members'].append({'run': entry['run'], 'complete': member['complete'], 'harbor_returncode': member.get('harbor_returncode'), 'error': member.get('error'), 'launch_result': io.evidence_ref(Path(entry['run']) / 'launch-result.json', root)})
                    io.write_json(directory / 'runtime-status.json', state)
                    alive(server)
                state['complete'] = all((member['complete'] for member in state['members']))
        except BaseException as exc:
            state['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            stop(server)
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
            state['finished_at'] = time.time()
            state['started_member_count'] = sum(((Path(e['run']) / 'launch.json').exists() for e in manifest['identity']['members']))
            state['unstarted_members'] = [e['run'] for e in manifest['identity']['members'] if not (Path(e['run']) / 'launch.json').exists()]
            io.write_json(directory / 'launch-result.json', state)
        return state
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    result = run_batch(parser.parse_args().batch)
    print(json.dumps({'complete': result['complete'], 'members': len(result['members']), 'model_requests_outside_measurement': 0, 'training_prohibited': True}))
