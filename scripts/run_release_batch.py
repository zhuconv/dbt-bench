#!/usr/bin/env python3
"""Run prepared native Harbor jobs sequentially with one locked model service.

This is a finite launch wrapper, not a scheduler: no retries, new attempts,
budget overrides, synthetic requests, or automatic continuation allocations.
Version3 binds an independent patched runtime and either a single recovery
slot (5h) or at most three four-attempt members (24h). Old plans are rejected.
"""
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
from run_gh_smoke import stop

PURPOSE = 'difficulty_only_no_training'
SLURM_SCRIPT = Path('scripts/slurm/gh-difficulty-batch-v3.sbatch')
ALLOCATION_POLICY = {'allowed_walltime_sec': [18000, 86400], 'exact_prepared_walltime': True}


class ServiceDied(RuntimeError):
    pass


class BatchInterrupted(RuntimeError):
    pass


def scratch(path):
    from dbt_bench.local_config import required
    path=Path(path).resolve()
    if not path.is_relative_to(Path(required()['artifact_root']).resolve()):
        raise ValueError('Run must be within configured artifact_root')
    return path


def unlaunched(directory):
    for name in ('launch.json','launch-result.json','runtime-status.json','jobs'):
        if (directory/name).exists():raise ValueError('Existing run must never be restarted')


def slurm_seconds(value):
    """Parse finite scontrol D-HH:MM:SS or HH:MM:SS without lossy coercion."""
    match = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d{2}):(\d{2})", value or '')
    if not match:
        raise ValueError('Unrecognized finite Slurm duration: ' + str(value))
    days, hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60 or (days is not None and int(hours) >= 24):
        raise ValueError('Invalid Slurm duration: ' + value)
    return (int(days or 0) * 24 + int(hours)) * 3600 + int(minutes) * 60 + int(seconds)


def durable_json(path, value):
    """Atomic durable checkpoint; unique temporary name permits signal reentry."""
    path = Path(path)
    temporary = path.with_name(path.name + '.partial-' + str(os.getpid()) + '-' + str(time.time_ns()))
    try:
        with temporary.open('x') as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def finalization_error(state, stage, exc):
    state.setdefault('finalization_errors', []).append(
        {'stage': stage, 'type': type(exc).__name__, 'message': str(exc)})


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
        self.directory, self.manifest, self.state = Path(directory), manifest, state
        self.active = None
        self.member_state = None
        self.event = None

    def member_lists(self):
        runs = [entry['run'] for entry in self.manifest['identity']['members']]
        started = [run for run in runs if (Path(run) / 'launch.json').exists()]
        return started, [run for run in runs if run not in started]

    def handle(self, number, frame):
        if self.event is not None:
            # Cleanup may still be running. Never interrupt its finally blocks twice.
            return
        observed_at = time.time()
        started, unstarted = self.member_lists()
        self.event = {'kind': 'external_process_signal', 'signal_number': int(number),
            'signal_name': signal.Signals(number).name, 'observed_at': observed_at,
            'active_member': str(self.active) if self.active else None,
            'current_member': str(self.active) if self.active else None,
            'local_utc_offset_sec': datetime.now().astimezone().utcoffset().total_seconds(),
            'batch': str(self.directory), 'experiment_id': self.manifest['experiment_id'],
            'slurm_job_id': self.state['slurm_job_id'], 'started_members': started,
            'unstarted_members': unstarted, 'model_requests_outside_measurement': 0}
        # No hashing, globbing result files, subprocess calls or process waits here.
        try:
            durable_json(self.directory / 'interruption.json', self.event)
        except Exception as exc:
            finalization_error(self.state, 'interruption_event', exc)
            print('Cannot persist interruption event: ' + repr(exc), file=sys.stderr, flush=True)
        self.state.update(interruption=self.event, active_member=self.event['active_member'],
                          unstarted_members=unstarted, complete=False,
                          finalization_status='interrupted_before_cleanup')
        checkpoint(self.directory, self.state)
        if self.active is not None and self.member_state is not None:
            self.member_state.update(interruption=self.event, complete=False,
                                     finalization_status='interrupted_before_cleanup')
            checkpoint(self.active, self.member_state)
        raise BatchInterrupted('Allocation/process signal ' + str(number))

    def raise_if_interrupted(self):
        if self.event is not None:
            raise BatchInterrupted('Allocation/process signal ' + str(self.event['signal_number']))


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
    # Exclusive creation also closes the race between validation and launch.
    with (directory / 'launch.json').open('x') as handle:
        json.dump(state, handle, sort_keys=True, indent=2)
        handle.write('\n')


def state_for(directory, manifest, job, batch, root):
    shutil.copyfile(__file__, directory / 'runner.py')
    return {'schema_version': 1, 'slurm_job_id': job, 'hostname': socket.gethostname(),
            'started_at': time.time(), 'experiment_id': manifest['experiment_id'],
            'runner': io.evidence_ref(directory / 'runner.py', root),
            'helper': io.evidence_ref(root / 'scripts/run_gh_smoke.py', root),
            'planned': manifest.get('planned', manifest['identity'].get('planned')),
            'training_prohibited': True, 'protocol_model_requests': 0, 'complete': False,
            'shared_model_service': True, 'shared_model_batch': str(batch),
            'batch_manifest': io.evidence_ref(batch / 'manifest.json', root),
            'server_seed': manifest['identity']['server_seed'],
            'runtime_patch': manifest['identity']['runtime_patch'],
            'recovery': manifest['identity'].get('recovery')}


def terminal_trials(directory, config, state, root):
    paths = sorted((directory / 'jobs' / config['job_name']).glob('*/result.json'))
    records, errors, references = [], [], []
    for path in paths:
        try:
            reference = io.evidence_ref(path, root)
            references.append(reference)
            record = io.read_json(path)
            if not record.get('id') or not record.get('trial_name') or not record.get('finished_at'):
                errors.append({'path': str(path), 'reason': 'Not a complete terminal native trial'})
            else:
                records.append({'id': record['id'], 'trial_name': record['trial_name'],
                    'started_at': record.get('started_at'), 'finished_at': record['finished_at'],
                    'result': reference})
        except (ValueError, OSError, TypeError) as exc:
            errors.append({'path': str(path), 'reason': str(exc)})
    if len({r['id'] for r in records}) != len(records):
        errors.append({'reason': 'Duplicate native trial ID'})
    state['raw_trials'] = references
    state['retained_terminal_trials'] = records
    state['native_terminal_check'] = {'expected': len(state['planned']), 'actual_files': len(paths),
        'terminal_trials': len(records), 'errors': errors,
        'passed': len(paths) == len(state['planned']) and len(records) == len(state['planned']) and not errors}
    state['complete'] = state['native_terminal_check']['passed']
    state['audit_status'] = 'pending_posthoc_submission_audit'


def cleanup(directory, env, state, root):
    try:
        result = subprocess.run([sys.executable,
            str(root / 'scripts/verify_container_cleanup.py'), '--jobs', str(directory / 'jobs'),
            '--output', str(directory / 'cleanup.json')], cwd=root, env=env,
            capture_output=True, text=True, timeout=120)
        (directory / 'cleanup.log').write_text(result.stdout + result.stderr)
        state['cleanup_returncode'] = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        state['cleanup_error'] = {'type': type(exc).__name__, 'message': str(exc)}


def run_member(entry, batch, batch_manifest, server, env, job, root, journal, allocation_info):
    directory = Path(entry['run'])
    journal.active, journal.member_state = directory, None
    manifest, config, _ = validate_member(entry, batch, batch_manifest['identity'], root)
    alive(server); journal.raise_if_interrupted()
    state = state_for(directory, manifest, job, batch, root)
    state.update(server_pid=server.pid, allocation=allocation_info,
                 server_ready_at=io.read_json(batch / 'runtime-status.json')['server_ready_at'])
    claim(directory, state)
    journal.active, journal.member_state = directory, state
    harbor = None
    stop_error = None
    try:
        with (directory / 'harbor.log').open('w') as handle:
            harbor = subprocess.Popen([str(Path(sys.executable).with_name('harbor')), 'run', '-c', str(directory / 'config.yaml')],
                cwd=root, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            state.update(harbor_pid=harbor.pid, harbor_started_at=time.time())
            durable_json(directory / 'runtime-status.json', state)
            while harbor.poll() is None:
                alive(server); journal.raise_if_interrupted()
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
        # Every operation is isolated. A cleanup error cannot skip the main record.
        try:
            stop(harbor)
        except BaseException as exc:
            stop_error = exc; finalization_error(state, 'stop_harbor', exc)
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
        journal.active, journal.member_state = None, None
    journal.raise_if_interrupted()
    if stop_error is not None:
        raise RuntimeError('Cannot confirm native job process stopped; no next member') from stop_error
    return state


def run_batch(directory, root=ROOT):
    directory, root = scratch(directory), Path(root)
    job = os.environ.get('SLURM_JOB_ID')
    if not job:
        raise ValueError('An actual Slurm allocation is required')
    with cached_hashes():
        # Refuse old plans before any write. These small seals are checked before
        # claiming; expensive task/image validation is protected by the journal.
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
            durable_json(directory / 'cache-paths.json', {key: server_env[key]
                for key in ('VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR', 'FLASHINFER_WORKSPACE_BASE')})
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 8000))
            with (directory / 'vllm.log').open('w') as handle:
                server = subprocess.Popen(command, cwd=root, env=server_env, stdout=handle,
                                          stderr=subprocess.STDOUT, start_new_session=True)
                state['server_pid'] = server.pid
                wait_ready(server, model, directory)
                state['server_ready_at'] = time.time()
                durable_json(directory / 'runtime-status.json', state)
                for entry in manifest['identity']['members']:
                    journal.raise_if_interrupted()
                    state['active_member'] = entry['run']
                    member = run_member(entry, directory, manifest, server, env, job, root,
                                        journal, allocation_info)
                    state['members'].append({'run': entry['run'], 'complete': member['complete'],
                        'harbor_returncode': member.get('harbor_returncode'), 'error': member.get('error'),
                        'launch_result': io.evidence_ref(Path(entry['run']) / 'launch-result.json', root)})
                    state['active_member'] = None
                    durable_json(directory / 'runtime-status.json', state)
                    alive(server)
                state['complete'] = all(member['complete'] for member in state['members'])
        except BaseException as exc:
            state['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            started, unstarted = journal.member_lists()
            state.update(started_member_count=len(started), unstarted_members=unstarted,
                         finalization_status='cleanup_pending')
            checkpoint(directory, state)
            try:
                stop(server)
            except BaseException as exc:
                finalization_error(state, 'stop_server', exc)
            # Persist even if any child/container stop failed or another TERM arrived.
            state.update(finished_at=time.time(), finalization_status='recorded_after_cleanup')
            checkpoint(directory, state)
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
        journal.raise_if_interrupted()
        return state


# Deployment-specific validation and coordinates; native orchestration above is reused.
from release_tools import (preflight, validate_member, validate_batch, allocation,
                           environment, model_environment, model_command)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True)
    args=parser.parse_args();result=run_batch(args.batch)
    print(json.dumps({'complete':result['complete'],'training_prohibited':True}))
