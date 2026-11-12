"""Finite shared-service orchestration controls; no model or scheduler calls."""
from dbt_bench.local_config import expand as _release_expand
import importlib.util
import io as stdio
import json
import os
import signal
import subprocess
import select
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
AVAILABLE = sys.version_info >= (3, 11) and importlib.util.find_spec('yaml') is not None and (importlib.util.find_spec('tomllib') is not None)
if AVAILABLE:
    import run_difficulty_gh_batch_v3 as runner
    import run_difficulty_gh_batch as previous_runner
    from dbt_bench import io

@unittest.skipUnless(AVAILABLE, 'Python>=3.11 plus locked runtime PyYAML/tomllib dependencies are required')
class BatchControls(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gh-batch-controls-')
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.scratch = patch.object(runner, 'scratch', side_effect=lambda path: Path(path).resolve())
        self.scratch.start()
        self.addCleanup(self.scratch.stop)
        self.native = patch.object(runner, 'validate_native_member', side_effect=self.native_validate)
        self.native.start()
        self.addCleanup(self.native.stop)
        self.runtime = patch.object(runner, 'validate_runtime', return_value={})
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.prepare()

    def native_validate(self, path, root):
        return (io.read_json(path / 'manifest.json'), io.read_json(path / 'config.yaml'), {})

    def seal(self, path):
        io.write_json(path / 'preflight.json', {'runner': io.evidence_ref(Path(runner.__file__), ROOT), 'slurm_script': io.evidence_ref(ROOT / runner.SLURM_SCRIPT, ROOT), 'manifest': io.evidence_ref(path / 'manifest.json', ROOT), 'allocation_policy': dict(runner.ALLOCATION_POLICY), 'execution_dependencies': [io.evidence_ref(ROOT / 'scripts' / name, ROOT) for name in ('run_difficulty_gpu_v2.py', 'run_gh_smoke.py', 'review_tools.py')]})

    def prepare(self):
        rules = self.directory / 'rules.json'
        io.write_json(rules, {'attempts_per_task': 4})
        amendment = self.directory / 'amendment.json'
        io.write_json(amendment, {'account': _release_expand('${ALLOCATION}'), 'partition': 'gh', 'walltime_sec': 86400, 'allowed_walltime_sec': [18000, 86400], 'authorized_attempt_ids': [1, 2, 3, 4], 'runtime_patch': {}, 'training_prohibited': True})
        identity = {'measurement_rules': io.evidence_ref(rules, ROOT), 'scheduling_amendment': io.evidence_ref(amendment, ROOT), 'server_seed': 1234, 'walltime_sec': 86400, 'allowed_walltime_sec': [18000, 86400], 'authorized_attempt_ids': [1, 2, 3, 4], 'runtime_patch': {}, 'members': [], 'planned': []}
        for task in ('ade__first', 'deb__second'):
            directory = self.directory / 'members' / task
            directory.mkdir(parents=True)
            config = {'n_attempts': 4, 'job_name': task, 'jobs_dir': str(directory / 'jobs')}
            member = {**identity, 'task_key': task, 'attempt_ids': [1, 2, 3, 4], 'shared_model_batch': str(self.directory), 'config': config}
            member.pop('members')
            member.pop('planned')
            slots = [{'task_key': task, 'attempt': n} for n in (1, 2, 3, 4)]
            manifest = {'identity': member, 'experiment_id': io.digest(member), 'planned': slots, 'training_prohibited': True}
            io.write_json(directory / 'config.yaml', config)
            io.write_json(directory / 'manifest.json', manifest)
            self.seal(directory)
            identity['members'].append({'run': str(directory), 'manifest': io.evidence_ref(directory / 'manifest.json', ROOT)})
            identity['planned'].extend(slots)
        self.manifest = {'schema_version': 1, 'purpose': runner.PURPOSE, 'training_prohibited': True, 'identity': identity, 'experiment_id': io.digest(identity)}
        io.write_json(self.directory / 'manifest.json', self.manifest)
        self.seal(self.directory)

    def execute(self, *, harbor_codes=(0, 0), fail=None):
        starts, stops, cleanups = ([], [], [])

        class Server:
            pid = 771
            returncode = None

            def poll(self):
                return self.returncode
        server = Server()

        class Harbor:

            def __init__(self, directory, code):
                self.pid = 800 + len(starts)
                self.returncode = None
                self.code = code
                self.polls = 0
                for n in range(4 if code == 0 and fail != 'signal' else 2):
                    io.write_json(directory / 'jobs' / directory.name / ('trial-' + str(n)) / 'result.json', {'id': directory.name + str(n), 'trial_name': 'trial-' + str(n), 'finished_at': '2026-11-09T00:00:00Z'})

            def poll(self):
                self.polls += 1
                if fail == 'server_dies' and self.polls == 1:
                    server.returncode = 9
                if fail == 'signal' and self.polls == 1:
                    signal.raise_signal(signal.SIGTERM)
                if self.polls > 1:
                    self.returncode = self.code
                return self.returncode

        def popen(argv, **kwargs):
            starts.append(argv)
            if argv[0] == 'fake-vllm':
                return server
            if fail == 'harbor_spawn':
                raise OSError('simulated native job process failure')
            path = Path(argv[-1]).parent
            return Harbor(path, harbor_codes[len(starts) - 2])

        def stop(process):
            if process is not None:
                stops.append(process.pid)
                if fail == 'stop_error':
                    raise RuntimeError('simulated process cleanup failure')
                if fail == 'signal':
                    event = io.read_json(self.directory / 'interruption.json')
                    self.assertEqual(event['kind'], 'external_process_signal')
                    self.assertTrue((self.directory / 'launch-result.json').exists())
                    if process.pid != 771:
                        signal.raise_signal(signal.SIGTERM)
                        raise RuntimeError('cleanup failure after durable event')
                process.returncode = -15

        def run(argv, **kwargs):
            if 'verify_container_cleanup.py' in ' '.join(argv):
                cleanups.append(argv)
                io.write_json(Path(argv[-1]), {'passed': True, 'checks': []})
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        self.starts, self.stops, self.cleanups = (starts, stops, cleanups)
        with patch.dict(os.environ, {'SLURM_JOB_ID': '123'}), patch.object(runner, 'allocation', return_value={'raw': 'test allocation', 'fields': {'Account': _release_expand('${ALLOCATION_LOWER}'), 'TimeLimit': '1-00:00:00'}, 'actual_walltime_sec': 86400, 'time_min_sec': 86400}), patch.object(runner, 'environment', return_value={}), patch.object(runner, 'model_command', return_value=(['fake-vllm'], 'locked-model')), patch.object(runner, 'model_environment', return_value={'VLLM_CACHE_ROOT': str(self.directory / 'cache/vllm'), 'TRITON_CACHE_DIR': str(self.directory / 'cache/triton'), 'FLASHINFER_WORKSPACE_BASE': str(self.directory / 'cache/flashinfer')}), patch.object(runner, 'wait_ready', side_effect=RuntimeError('startup failure') if fail == 'startup' else None), patch.object(runner.socket, 'socket'), patch.object(runner.subprocess, 'Popen', side_effect=popen), patch.object(runner.subprocess, 'run', side_effect=run), patch.object(runner, 'stop', side_effect=stop), patch.object(runner.time, 'sleep'):
            return runner.run_batch(self.directory)

    def test_one_service_two_native_jobs_and_no_restart(self):
        state = self.execute()
        self.assertTrue(state['complete'])
        self.assertEqual([x[0] for x in self.starts].count('fake-vllm'), 1)
        self.assertEqual(len(self.starts), 3)
        self.assertEqual(len(self.cleanups), 2)
        self.assertEqual(self.stops[-1], 771)
        self.assertEqual(state['started_member_count'], 2)
        for entry in self.manifest['identity']['members']:
            result = io.read_json(Path(entry['run']) / 'launch-result.json')
            self.assertEqual(result['native_terminal_check']['terminal_trials'], 4)
            self.assertTrue(result['shared_model_service'])
            self.assertEqual(result['server_seed'], 1234)
            self.assertEqual(result['protocol_model_requests'], 0)
        with self.assertRaisesRegex(ValueError, 'never restarted'):
            self.execute()
        self.assertEqual(self.starts, [])

    def test_nonzero_native_exit_retained_and_next_member_runs(self):
        state = self.execute(harbor_codes=(1, 0))
        self.assertFalse(state['complete'])
        self.assertEqual([m['harbor_returncode'] for m in state['members']], [1, 0])
        self.assertEqual(len(self.starts), 3)
        self.assertEqual(state['unstarted_members'], [])

    def test_startup_exception_stops_service_and_starts_no_native_job(self):
        with self.assertRaisesRegex(RuntimeError, 'startup failure'):
            self.execute(fail='startup')
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.stops, [771])
        self.assertEqual(io.read_json(self.directory / 'launch-result.json')['started_member_count'], 0)

    def test_missing_imported_dependency_seal_rejected(self):
        seal = io.read_json(self.directory / 'preflight.json')
        seal.pop('execution_dependencies')
        io.write_json(self.directory / 'preflight.json', seal)
        with self.assertRaisesRegex(ValueError, 'dependency bindings'):
            runner.validate_batch(self.directory)

    def test_native_spawn_error_retained_without_retry(self):
        state = self.execute(fail='harbor_spawn')
        self.assertEqual(len(self.starts), 3)
        self.assertFalse(state['complete'])
        self.assertEqual([m['error']['type'] for m in state['members']], ['OSError', 'OSError'])
        self.assertIn(771, self.stops)

    def test_server_death_stops_current_job_without_next_member(self):
        with self.assertRaises(runner.ServiceDied):
            self.execute(fail='server_dies')
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(len(self.stops), 2)
        state = io.read_json(self.directory / 'launch-result.json')
        self.assertFalse(state['complete'])
        self.assertEqual(len(state['unstarted_members']), 1)
        first = Path(self.manifest['identity']['members'][0]['run'])
        self.assertEqual(io.read_json(first / 'launch-result.json')['error']['type'], 'ServiceDied')

    def test_signal_preserves_results_and_stops_both_owned_processes(self):
        with self.assertRaises(runner.BatchInterrupted):
            self.execute(fail='signal')
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(len(self.stops), 2)
        state = io.read_json(self.directory / 'launch-result.json')
        self.assertEqual(state['started_member_count'], 1)
        event = io.read_json(self.directory / 'interruption.json')
        first = Path(self.manifest['identity']['members'][0]['run'])
        self.assertEqual(event['active_member'], str(first))
        self.assertEqual(event['current_member'], str(first))
        self.assertIsInstance(event['local_utc_offset_sec'], (int, float))
        self.assertEqual(event['signal_number'], signal.SIGTERM)
        self.assertEqual(len(event['unstarted_members']), 1)
        member = io.read_json(first / 'launch-result.json')
        self.assertEqual(len(member['retained_terminal_trials']), 2)
        self.assertTrue(member['finalization_errors'])
        self.assertEqual(state['finalization_status'], 'recorded_after_cleanup')

    def test_changed_member_manifest_ref_rejected_before_start(self):
        first = Path(self.manifest['identity']['members'][0]['run']) / 'manifest.json'
        first.write_text(first.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.execute()
        self.assertEqual(self.starts, [])

    def test_duplicate_member_rejected(self):
        identity = self.manifest['identity']
        identity['members'].append(identity['members'][0])
        self.manifest['experiment_id'] = io.digest(identity)
        io.write_json(self.directory / 'manifest.json', self.manifest)
        self.seal(self.directory)
        with self.assertRaisesRegex(ValueError, 'Duplicate batch member'):
            runner.validate_batch(self.directory)

    def test_shared_server_seed_mismatch_rejected(self):
        entry = self.manifest['identity']['members'][0]
        directory = Path(entry['run'])
        member = io.read_json(directory / 'manifest.json')
        member['identity']['server_seed'] = 3
        io.write_json(directory / 'manifest.json', member)
        self.seal(directory)
        entry['manifest'] = io.evidence_ref(directory / 'manifest.json', ROOT)
        self.manifest['experiment_id'] = io.digest(self.manifest['identity'])
        io.write_json(self.directory / 'manifest.json', self.manifest)
        self.seal(self.directory)
        with self.assertRaisesRegex(ValueError, 'shared service'):
            runner.validate_batch(self.directory)

    def test_models_only_readiness(self):
        server = type('Server', (), {'poll': lambda self: None})()
        with patch.object(runner.urllib.request, 'urlopen', return_value=stdio.BytesIO(b'{"data":[{"id":"locked-model"}]}')) as request:
            runner.wait_ready(server, 'locked-model', self.directory)
        self.assertEqual(request.call_args.args, ('http://127.0.0.1:8000/v1/models',))
        self.assertEqual(request.call_count, 1)

    def test_compilation_caches_separated_without_changing_model_cache(self):
        with patch.object(runner, 'inference_environment', return_value={'HF_HOME': '/existing/readonly/cache'}):
            env = runner.model_environment(self.directory, {}, ROOT)
        self.assertEqual(env['HF_HOME'], '/existing/readonly/cache')
        for name in ('VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR', 'FLASHINFER_WORKSPACE_BASE'):
            self.assertTrue(Path(env[name]).is_relative_to(self.directory / 'cache'))
            self.assertTrue(Path(env[name]).is_dir())
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')

    def test_actual_allocation_retained_in_batch_and_member(self):
        state = self.execute()
        self.assertEqual(state['allocation']['actual_walltime_sec'], 86400)
        self.assertEqual(io.read_json(self.directory / 'allocation.json')['fields']['Account'], _release_expand('${ALLOCATION_LOWER}'))
        for entry in self.manifest['identity']['members']:
            self.assertEqual(io.read_json(Path(entry['run']) / 'launch.json')['allocation']['actual_walltime_sec'], 86400)

    def test_cleanup_exception_cannot_erase_primary_records_or_start_next_member(self):
        with self.assertRaisesRegex(RuntimeError, 'Cannot confirm'):
            self.execute(fail='stop_error')
        state = io.read_json(self.directory / 'launch-result.json')
        first = Path(self.manifest['identity']['members'][0]['run'])
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(len(state['unstarted_members']), 1)
        self.assertTrue(state['finalization_errors'])
        member = io.read_json(first / 'launch-result.json')
        self.assertEqual(len(member['retained_terminal_trials']), 4)
        self.assertTrue(member['finalization_errors'])
        self.assertEqual(member['native_terminal_check']['terminal_trials'], 4)

    def test_old_preflight_or_missing_allocation_policy_never_silently_upgrades(self):
        p = self.directory / 'preflight.json'
        seal = io.read_json(p)
        seal['runner'] = io.evidence_ref(Path(previous_runner.__file__), ROOT)
        io.write_json(p, seal)
        with self.assertRaisesRegex(ValueError, 'different runner'):
            runner.validate_batch(self.directory)
        self.seal(self.directory)
        seal = io.read_json(p)
        seal.pop('allocation_policy')
        io.write_json(p, seal)
        with self.assertRaisesRegex(ValueError, 'v3 exact allocation'):
            runner.validate_batch(self.directory)

    def test_signal_during_member_validation_identifies_current_member(self):
        original = runner.validate_member
        count = 0

        def validate(entry, *args):
            nonlocal count
            count += 1
            if count == 3:
                signal.raise_signal(signal.SIGTERM)
            return original(entry, *args)
        with patch.object(runner, 'validate_member', side_effect=validate):
            with self.assertRaises(runner.BatchInterrupted):
                self.execute()
        event = io.read_json(self.directory / 'interruption.json')
        self.assertEqual(event['current_member'], self.manifest['identity']['members'][0]['run'])
        self.assertEqual(len(event['unstarted_members']), 2)
        self.assertEqual(len(self.starts), 1)

    def test_signal_during_initial_image_validation_is_already_journalled(self):
        with patch.object(runner, 'validate_native_member', side_effect=lambda *args: signal.raise_signal(signal.SIGTERM)):
            with self.assertRaises(runner.BatchInterrupted):
                self.execute()
        event = io.read_json(self.directory / 'interruption.json')
        self.assertEqual(event['current_member'], self.manifest['identity']['members'][0]['run'])
        self.assertEqual(len(event['unstarted_members']), 2)
        self.assertEqual(self.starts, [])
        self.assertEqual(io.read_json(self.directory / 'launch-result.json')['started_member_count'], 0)

@unittest.skipUnless(AVAILABLE, 'Python>=3.11 plus locked runtime PyYAML/tomllib dependencies are required')
class AllocationControls(unittest.TestCase):

    @staticmethod
    def result(account=_release_expand('${ALLOCATION_LOWER}'), limit='1-00:00:00', time_min='1-00:00:00'):
        text = f'JobId=123 Account={account} Partition=gh TimeLimit={limit} TimeMin={time_min} NumNodes=1 NumTasks=1'
        return type('Result', (), {'stdout': text})()

    def test_exact5_or24_hours_and_casefold_account(self):
        for account in (_release_expand('${ALLOCATION_LOWER}'), _release_expand('${ALLOCATION}')):
            for limit, expected in [('1-00:00:00', 86400), ('24:00:00', 86400), ('05:00:00', 18000)]:
                with self.subTest(account=account, limit=limit), patch.object(runner.subprocess, 'run', return_value=self.result(account, limit)) as call:
                    actual = runner.allocation('123', expected)
                self.assertEqual(actual['actual_walltime_sec'], expected)
                self.assertEqual(actual['fields']['Account'], account)
                self.assertEqual(actual['time_min_sec'], 86400)
                self.assertEqual(call.call_args.args[0], ['scontrol', 'show', 'job', '-o', '123'])

    def test_invalid_limits_and_other_account_rejected(self):
        for account, limit in [('other', '24:00:00'), (_release_expand('${ALLOCATION_LOWER}'), '23:59:59'), (_release_expand('${ALLOCATION_LOWER}'), '2-00:00:01'), (_release_expand('${ALLOCATION_LOWER}'), 'UNLIMITED'), (_release_expand('${ALLOCATION_LOWER}'), '24:99:00')]:
            with self.subTest(account=account, limit=limit), patch.object(runner.subprocess, 'run', return_value=self.result(account, limit)):
                with self.assertRaises(ValueError):
                    runner.allocation('123', 86400)

    def test_legacy24h_and_lowercase48h_fail_before_launch(self):
        for account, limit in [(_release_expand('${ALLOCATION}'), '1-00:00:00'), (_release_expand('${ALLOCATION_LOWER}'), '2-00:00:00')]:
            with self.subTest(account=account, limit=limit), patch.object(previous_runner.subprocess, 'run', return_value=self.result(account, limit)):
                with self.assertRaises(ValueError):
                    previous_runner.allocation('123')

    def test_new_sbatch_is_separate_and_exact24h(self):
        text = (ROOT / runner.SLURM_SCRIPT).read_text()
        self.assertIn('#SBATCH --time=24:00:00', text)
        self.assertNotIn('#SBATCH --time-min', text)
        self.assertIn('#SBATCH --signal=B:TERM@180', text)
        self.assertIn('scripts/run_difficulty_gh_batch_v3.py', text)
        self.assertNotEqual(runner.SLURM_SCRIPT, previous_runner.SLURM_SCRIPT)

    def test_local_subprocess_real_term_durably_records_before_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='gh-v2-real-signal-') as directory:
            code = "\nimport signal,sys\nfrom pathlib import Path\nfrom run_difficulty_gh_batch_v3 import InterruptJournal,BatchInterrupted\np=Path(sys.argv[1]); member=p/'not-started'\nmanifest={'experiment_id':'synthetic-no-model','identity':{'members':[{'run':str(member)}]}}\nstate={'slurm_job_id':'synthetic-no-slurm'}\njournal=InterruptJournal(p,manifest,state)\nsignal.signal(signal.SIGTERM,journal.handle)\nprint('ready',flush=True)\ntry:\n    signal.pause()\nexcept BatchInterrupted:\n    assert (p/'interruption.json').exists()\n    assert (p/'launch-result.json').exists()\n    print('event-before-cleanup',flush=True)\n"
            env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src') + os.pathsep + str(ROOT / 'scripts'), 'PYTHONDONTWRITEBYTECODE': '1'}
            proc = subprocess.Popen([sys.executable, '-c', code, directory], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            try:
                ready, _, _ = select.select([proc.stdout], [], [], 5)
                self.assertTrue(ready, 'Short signal fixture failed to start')
                self.assertEqual(proc.stdout.readline().strip(), 'ready')
                proc.send_signal(signal.SIGTERM)
                stdout, stderr = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertIn('event-before-cleanup', stdout)
                event = json.loads((Path(directory) / 'interruption.json').read_text())
                self.assertEqual(event['kind'], 'external_process_signal')
                self.assertEqual(event['signal_number'], signal.SIGTERM)
                self.assertIsNone(event['current_member'])
                self.assertEqual(len(event['unstarted_members']), 1)
                self.assertIsInstance(event['observed_at'], float)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

class RecoveryControls(unittest.TestCase):

    def test_single_result_completes_only_single_planned_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            io.write_json(out / 'jobs' / 'test' / 'trial1' / 'result.json', {'id': 'one', 'trial_name': 'one', 'finished_at': '2026-11-12T00:00:00Z'})
            state = {'planned': [{'task_key': 'test', 'attempt': 1}]}
            runner.terminal_trials(out, {'job_name': 'test'}, state, ROOT)
            self.assertTrue(state['complete'])
            state['planned'].append({'task_key': 'test', 'attempt': 2})
            runner.terminal_trials(out, {'job_name': 'test'}, state, ROOT)
            self.assertFalse(state['complete'])

    def test_recovery_scope_cannot_include_retained_attempts(self):
        identity = {'walltime_sec': 18000, 'members': [{}], 'authorized_attempt_ids': [1, 2], 'recovery': {}}
        with self.assertRaisesRegex(ValueError, 'Only approved'):
            runner.validate_scope(identity, ROOT)

    def test_24h_rejects_partial_member(self):
        with self.assertRaisesRegex(ValueError, 'four attempts'):
            runner.validate_scope({'walltime_sec': 86400, 'members': [{}], 'authorized_attempt_ids': [1]}, ROOT)

    def test_changed_runtime_manifest_fails_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'manifest.json'
            file.write_text('{}')
            ref = io.evidence_ref(file, ROOT)
            file.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                runner.validate_runtime({'manifest': ref}, ROOT)
if __name__ == '__main__':
    unittest.main()
