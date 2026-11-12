"""Run prepared native Harbor jobs sequentially with one locked model service.

This is a finite launch wrapper, not a scheduler: no retries, new attempts,
budget overrides, synthetic requests, or automatic continuation allocations.
Version3 binds an independent patched runtime and either a single recovery
slot (5h) or at most three four-attempt members (24h). Old plans are rejected.
"""
from dbt_bench.local_config import expand as _release_expand
import argparse
from datetime import datetime
import json
import os
import re
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
SLURM_SCRIPT = Path('scripts/slurm/gh-difficulty-batch-v3.sbatch')
ALLOCATION_POLICY = {'allowed_walltime_sec': [18000, 86400], 'exact_prepared_walltime': True}

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
    if evidence.get('allocation_policy') != ALLOCATION_POLICY:
        raise ValueError('New preflight must bind the v3 exact allocation policy')
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
    if member.get('shared_model_batch') != str(batch) or member.get('server_seed') != identity['server_seed'] or member.get('measurement_rules') != identity['measurement_rules'] or (member.get('scheduling_amendment') != identity['scheduling_amendment']) or (member['attempt_ids'] != identity['authorized_attempt_ids']) or (config['n_attempts'] != len(identity['authorized_attempt_ids'])) or (member.get('runtime_patch') != identity['runtime_patch']):
        raise ValueError('Member differs from the shared service / four-attempt protocol')
    expected = [{'task_key': member['task_key'], 'attempt': n} for n in identity['authorized_attempt_ids']]
    if manifest['planned'] != expected:
        raise ValueError('Member planned slots differ from its actual configuration')
    preflight(directory, root)
    return (manifest, config, rules)

def validate_batch(directory, root=ROOT, *, claimed_state=None, journal=None):
    directory, root = (scratch(directory), Path(root))
    if claimed_state is None:
        unlaunched(directory)
    elif io.read_json(directory / 'launch.json') != claimed_state:
        raise ValueError('Claimed launch changed during validation')
    manifest = io.read_json(directory / 'manifest.json')
    identity = manifest['identity']
    if manifest.get('schema_version') != 1 or manifest.get('training_prohibited') is not True or manifest.get('purpose') != PURPOSE or (io.digest(identity) != manifest['experiment_id']) or (identity.get('walltime_sec') not in ALLOCATION_POLICY['allowed_walltime_sec']) or (type(identity.get('server_seed')) is not int):
        raise ValueError('Batch identity / authorization differs')
    for field in ('measurement_rules', 'scheduling_amendment'):
        unchanged(identity[field], root)
    amendment = io.read_json(root / identity['scheduling_amendment']['path'])
    if amendment.get('account') != _release_expand('${ALLOCATION}') or amendment.get('partition') != 'gh' or identity['walltime_sec'] not in amendment.get('allowed_walltime_sec', []) or (amendment.get('training_prohibited') is not True):
        raise ValueError('Scheduling amendment is not the authorized gh allocation')
    if identity.get('plan'):
        unchanged(identity['plan'], root)
    validate_scope(identity, root)
    validate_runtime(identity['runtime_patch'], root)
    preflight(directory, root)
    planned, directories = ([], set())
    for entry in identity['members']:
        run = str(Path(entry['run']).resolve())
        if run in directories:
            raise ValueError('Duplicate batch member')
        directories.add(run)
        if journal is not None:
            journal.active = Path(entry['run'])
        member, _, _ = validate_member(entry, directory, identity, root)
        planned.extend(member['planned'])
        if journal is not None:
            journal.active = None
    slots = [(row['task_key'], row['attempt']) for row in planned]
    if planned != identity['planned'] or len(slots) != len(set(slots)):
        raise ValueError('Duplicate, missing or reordered planned slots')
    return manifest

def validate_scope(identity, root):
    members = identity['members']
    slots = identity['authorized_attempt_ids']
    if identity['walltime_sec'] == 18000:
        recovery = identity.get('recovery', {})
        if len(members) != 1 or slots != [1] or recovery.get('replaces_trial') != 'NKMHv3F' or (recovery.get('task_key') != 'deb__dbt-fix-repeat-purchase-cohort-revenue') or (sorted(recovery.get('retained_slots', [])) != [2, 3, 4]) or (len(recovery.get('retained_results', [])) != 3):
            raise ValueError('Only approved NKMHv3F slot1 recovery may use5h')
        unchanged(recovery['adjudication'], root)
        unchanged(recovery['replaced_result'], root)
        for ref in recovery['retained_results']:
            unchanged(ref, root)
    elif not 1 <= len(members) <= 3 or slots != [1, 2, 3, 4] or identity.get('recovery'):
        raise ValueError('24h batches require1–3 tasks with four attempts each')

def validate_runtime(binding, root):
    unchanged(binding['manifest'], root)
    manifest = io.read_json(root / binding['manifest']['path'])
    identity = dict(manifest)
    runtime_id = identity.pop('runtime_id')
    if io.digest(identity) != runtime_id or binding['runtime_id'] != runtime_id:
        raise ValueError('Runtime identity differs')
    if str(Path(sys.executable).absolute()) != manifest['python']:
        raise ValueError('Runner must execute using the sealed independent Python')
    import harbor
    if Path(harbor.__file__).parent.resolve() != Path(manifest['package']).resolve():
        raise ValueError('Wrong imported Harbor package')
    for field in ('patch', 'base_interpreter', 'preparer', 'base_files', 'files', 'dependencies', 'source_lock'):
        unchanged(manifest[field], root)
    for field, directory in (('files', 'package'), ('base_files', 'base_package')):
        for item in io.read_json(root / manifest[field]['path']):
            if io.file_hash(Path(manifest[directory]) / item['path']) != item['sha256']:
                raise ValueError('Runtime file changed: ' + item['path'])
    for ref in binding['bootstrap_files']:
        unchanged(ref, root)
    unchanged(binding['cpu_release'], root)
    if not io.read_json(root / binding['cpu_release']['path'])['cpu_validation_passed']:
        raise ValueError('Runtime has not passed CPU release checks')
    return manifest

def slurm_seconds(value):
    """Parse finite scontrol D-HH:MM:SS or HH:MM:SS without lossy coercion."""
    match = re.fullmatch('(?:(\\d+)-)?(\\d+):(\\d{2}):(\\d{2})', value or '')
    if not match:
        raise ValueError('Unrecognized finite Slurm duration: ' + str(value))
    days, hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60 or (days is not None and int(hours) >= 24):
        raise ValueError('Invalid Slurm duration: ' + value)
    return (int(days or 0) * 24 + int(hours)) * 3600 + int(minutes) * 60 + int(seconds)

def allocation(job, expected_walltime):
    raw = subprocess.run(['scontrol', 'show', 'job', '-o', job], capture_output=True, text=True, check=True, timeout=30).stdout
    fields = dict((part.split('=', 1) for part in raw.split() if '=' in part))
    actual_seconds = slurm_seconds(fields.get('TimeLimit'))
    if fields.get('Account', '').casefold() != _release_expand('${ALLOCATION_LOWER}') or fields.get('Partition') != 'gh' or actual_seconds != expected_walltime or (actual_seconds not in ALLOCATION_POLICY['allowed_walltime_sec']) or (fields.get('NumNodes') != '1') or (fields.get('NumTasks') != '1'):
        raise ValueError(_release_expand('Require ${ALLOCATION} / gh / exact prepared5h or24h / one node / one task'))
    return {'raw': raw, 'fields': fields, 'actual_walltime_sec': actual_seconds, 'time_min_sec': slurm_seconds(fields['TimeMin']) if fields.get('TimeMin') not in (None, 'N/A') else None, 'policy': dict(ALLOCATION_POLICY)}

def durable_json(path, value):
    """Atomic durable checkpoint; unique temporary name permits signal reentry."""
    path = Path(path)
    temporary = path.with_name(path.name + '.partial-' + str(os.getpid()) + '-' + str(time.time_ns()))
    try:
        with temporary.open('x') as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)

def finalization_error(state, stage, exc):
    state.setdefault('finalization_errors', []).append({'stage': stage, 'type': type(exc).__name__, 'message': str(exc)})

def checkpoint(directory, state):
    """Persist before cleanup; report write failures without masking root error."""
    try:
        durable_json(Path(directory) / 'launch-result.json', state)
    except Exception as exc:
        finalization_error(state, 'checkpoint', exc)
        print('Cannot persist checkpoint: ' + repr(exc), file=sys.stderr, flush=True)

class InterruptJournal:
    """Write a small external event before stopping or inspecting any process."""

    def __init__(self, directory, manifest, state):
        self.directory, self.manifest, self.state = (Path(directory), manifest, state)
        self.active = None
        self.member_state = None
        self.event = None

    def member_lists(self):
        runs = [entry['run'] for entry in self.manifest['identity']['members']]
        started = [run for run in runs if (Path(run) / 'launch.json').exists()]
        return (started, [run for run in runs if run not in started])

    def handle(self, number, frame):
        if self.event is not None:
            return
        observed_at = time.time()
        started, unstarted = self.member_lists()
        self.event = {'kind': 'external_process_signal', 'signal_number': int(number), 'signal_name': signal.Signals(number).name, 'observed_at': observed_at, 'active_member': str(self.active) if self.active else None, 'current_member': str(self.active) if self.active else None, 'local_utc_offset_sec': datetime.now().astimezone().utcoffset().total_seconds(), 'batch': str(self.directory), 'experiment_id': self.manifest['experiment_id'], 'slurm_job_id': self.state['slurm_job_id'], 'started_members': started, 'unstarted_members': unstarted, 'model_requests_outside_measurement': 0}
        try:
            durable_json(self.directory / 'interruption.json', self.event)
        except Exception as exc:
            finalization_error(self.state, 'interruption_event', exc)
            print('Cannot persist interruption event: ' + repr(exc), file=sys.stderr, flush=True)
        self.state.update(interruption=self.event, active_member=self.event['active_member'], unstarted_members=unstarted, complete=False, finalization_status='interrupted_before_cleanup')
        checkpoint(self.directory, self.state)
        if self.active is not None and self.member_state is not None:
            self.member_state.update(interruption=self.event, complete=False, finalization_status='interrupted_before_cleanup')
            checkpoint(self.active, self.member_state)
        raise BatchInterrupted('Allocation/process signal ' + str(number))

    def raise_if_interrupted(self):
        if self.event is not None:
            raise BatchInterrupted('Allocation/process signal ' + str(self.event['signal_number']))

def environment(directory, job, root):
    env = {**os.environ, 'OPENAI_API_KEY': 'local-no-auth', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(root / 'src') + os.pathsep + str(root / 'scripts'), 'APPTAINER_TMPDIR': '/tmp/dbt-difficulty-' + job, 'OMP_NUM_THREADS': '8'}
    env.pop('LD_PRELOAD', None)
    env['PATH'] = str(Path(sys.executable).parent) + ':/opt/apps/tacc-apptainer/1.4.1/bin:' + env.get('PATH', '')
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
    return {'schema_version': 1, 'slurm_job_id': job, 'hostname': socket.gethostname(), 'started_at': time.time(), 'experiment_id': manifest['experiment_id'], 'runner': io.evidence_ref(directory / 'runner.py', root), 'helper': io.evidence_ref(root / 'scripts/run_gh_smoke.py', root), 'planned': manifest.get('planned', manifest['identity'].get('planned')), 'training_prohibited': True, 'protocol_model_requests': 0, 'complete': False, 'shared_model_service': True, 'shared_model_batch': str(batch), 'batch_manifest': io.evidence_ref(batch / 'manifest.json', root), 'server_seed': manifest['identity']['server_seed'], 'runtime_patch': manifest['identity']['runtime_patch'], 'recovery': manifest['identity'].get('recovery')}

def terminal_trials(directory, config, state, root):
    paths = sorted((directory / 'jobs' / config['job_name']).glob('*/result.json'))
    records, errors, references = ([], [], [])
    for path in paths:
        try:
            reference = io.evidence_ref(path, root)
            references.append(reference)
            record = io.read_json(path)
            if not record.get('id') or not record.get('trial_name') or (not record.get('finished_at')):
                errors.append({'path': str(path), 'reason': 'Not a complete terminal native trial'})
            else:
                records.append({'id': record['id'], 'trial_name': record['trial_name'], 'started_at': record.get('started_at'), 'finished_at': record['finished_at'], 'result': reference})
        except (ValueError, OSError, TypeError) as exc:
            errors.append({'path': str(path), 'reason': str(exc)})
    if len({r['id'] for r in records}) != len(records):
        errors.append({'reason': 'Duplicate native trial ID'})
    state['raw_trials'] = references
    state['retained_terminal_trials'] = records
    state['native_terminal_check'] = {'expected': len(state['planned']), 'actual_files': len(paths), 'terminal_trials': len(records), 'errors': errors, 'passed': len(paths) == len(state['planned']) and len(records) == len(state['planned']) and (not errors)}
    state['complete'] = state['native_terminal_check']['passed']
    state['audit_status'] = 'pending_posthoc_submission_audit'

def cleanup(directory, env, state, root):
    try:
        result = subprocess.run([sys.executable, str(root / 'scripts/verify_container_cleanup.py'), '--jobs', str(directory / 'jobs'), '--output', str(directory / 'cleanup.json')], cwd=root, env=env, capture_output=True, text=True, timeout=120)
        (directory / 'cleanup.log').write_text(result.stdout + result.stderr)
        state['cleanup_returncode'] = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        state['cleanup_error'] = {'type': type(exc).__name__, 'message': str(exc)}

def run_member(entry, batch, batch_manifest, server, env, job, root, journal, allocation_info):
    directory = Path(entry['run'])
    journal.active, journal.member_state = (directory, None)
    manifest, config, _ = validate_member(entry, batch, batch_manifest['identity'], root)
    alive(server)
    journal.raise_if_interrupted()
    state = state_for(directory, manifest, job, batch, root)
    state.update(server_pid=server.pid, allocation=allocation_info, server_ready_at=io.read_json(batch / 'runtime-status.json')['server_ready_at'])
    claim(directory, state)
    journal.active, journal.member_state = (directory, state)
    harbor = None
    stop_error = None
    try:
        with (directory / 'harbor.log').open('w') as handle:
            harbor = subprocess.Popen([str(Path(sys.executable).with_name('harbor')), 'run', '-c', str(directory / 'config.yaml')], cwd=root, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            state.update(harbor_pid=harbor.pid, harbor_started_at=time.time())
            durable_json(directory / 'runtime-status.json', state)
            while harbor.poll() is None:
                alive(server)
                journal.raise_if_interrupted()
                time.sleep(1)
            state['harbor_returncode'] = harbor.returncode
            alive(server)
    except BaseException as exc:
        state['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        if isinstance(exc, (ServiceDied, BatchInterrupted, KeyboardInterrupt, SystemExit)):
            raise
    finally:
        state['finalization_status'] = 'cleanup_pending'
        checkpoint(directory, state)
        try:
            stop(harbor)
        except BaseException as exc:
            stop_error = exc
            finalization_error(state, 'stop_harbor', exc)
        try:
            terminal_trials(directory, config, state, root)
        except BaseException as exc:
            finalization_error(state, 'terminal_trials', exc)
        checkpoint(directory, state)
        try:
            cleanup(directory, env, state, root)
        except BaseException as exc:
            finalization_error(state, 'container_cleanup', exc)
        state.update(finished_at=time.time(), finalization_status='recorded_after_cleanup')
        checkpoint(directory, state)
        journal.active, journal.member_state = (None, None)
    journal.raise_if_interrupted()
    if stop_error is not None:
        raise RuntimeError('Cannot confirm native job process stopped; no next member') from stop_error
    return state

def run_batch(directory, root=ROOT):
    directory, root = (scratch(directory), Path(root))
    job = os.environ.get('SLURM_JOB_ID')
    if not job:
        raise ValueError('An actual Slurm allocation is required')
    with cached_hashes():
        unlaunched(directory)
        preflight(directory, root)
        manifest = io.read_json(directory / 'manifest.json')
        state = state_for(directory, manifest, job, directory, root)
        state.update(allocation=None, members=[], active_member=None)
        claim(directory, state)
        claimed_state = io.read_json(directory / 'launch.json')
        journal = InterruptJournal(directory, manifest, state)
        server = None
        previous_handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
        for number in previous_handlers:
            signal.signal(number, journal.handle)
        try:
            allocation_info = allocation(job, manifest['identity']['walltime_sec'])
            state['allocation'] = allocation_info
            (directory / 'allocation.txt').write_text(allocation_info['raw'])
            durable_json(directory / 'allocation.json', allocation_info)
            validate_batch(directory, root, claimed_state=claimed_state, journal=journal)
            env = environment(directory, job, root)
            with (directory / 'gpu.txt').open('w') as handle:
                subprocess.run(['nvidia-smi', '-q'], stdout=handle, stderr=subprocess.STDOUT, check=True, timeout=30)
            command, model = model_command(directory, manifest, root)
            server_env = model_environment(directory, env, root)
            durable_json(directory / 'cache-paths.json', {key: server_env[key] for key in ('VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR', 'FLASHINFER_WORKSPACE_BASE')})
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 8000))
            with (directory / 'vllm.log').open('w') as handle:
                server = subprocess.Popen(command, cwd=root, env=server_env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
                state['server_pid'] = server.pid
                wait_ready(server, model, directory)
                state['server_ready_at'] = time.time()
                durable_json(directory / 'runtime-status.json', state)
                for entry in manifest['identity']['members']:
                    journal.raise_if_interrupted()
                    state['active_member'] = entry['run']
                    member = run_member(entry, directory, manifest, server, env, job, root, journal, allocation_info)
                    state['members'].append({'run': entry['run'], 'complete': member['complete'], 'harbor_returncode': member.get('harbor_returncode'), 'error': member.get('error'), 'launch_result': io.evidence_ref(Path(entry['run']) / 'launch-result.json', root)})
                    state['active_member'] = None
                    durable_json(directory / 'runtime-status.json', state)
                    alive(server)
                state['complete'] = all((member['complete'] for member in state['members']))
        except BaseException as exc:
            state['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            started, unstarted = journal.member_lists()
            state.update(started_member_count=len(started), unstarted_members=unstarted, finalization_status='cleanup_pending')
            checkpoint(directory, state)
            try:
                stop(server)
            except BaseException as exc:
                finalization_error(state, 'stop_server', exc)
            state.update(finished_at=time.time(), finalization_status='recorded_after_cleanup')
            checkpoint(directory, state)
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
        journal.raise_if_interrupted()
        return state
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        with cached_hashes():
            validated = validate_batch(args.batch)
        print(json.dumps({'passed': True, 'experiment_id': validated['experiment_id'], 'model_requests': 0, 'training_prohibited': True}))
        sys.exit(0)
    result = run_batch(args.batch)
    print(json.dumps({'complete': result['complete'], 'members': len(result['members']), 'model_requests_outside_measurement': 0, 'training_prohibited': True}))
