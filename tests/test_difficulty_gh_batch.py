"""Finite shared-service orchestration controls; no model or scheduler calls."""
from dbt_bench.local_config import expand as _release_expand
import importlib.util
import io as stdio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
AVAILABLE = importlib.util.find_spec('yaml') is not None
if AVAILABLE:
    import run_difficulty_gh_batch as runner
    from dbt_bench import io

@unittest.skipUnless(AVAILABLE, 'Locked runtime PyYAML is required by the native runner dependency')
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
        self.prepare()

    def native_validate(self, path, root):
        return (io.read_json(path / 'manifest.json'), io.read_json(path / 'config.yaml'), {})

    def seal(self, path):
        io.write_json(path / 'preflight.json', {'runner': io.evidence_ref(Path(runner.__file__), ROOT), 'slurm_script': io.evidence_ref(ROOT / runner.SLURM_SCRIPT, ROOT), 'manifest': io.evidence_ref(path / 'manifest.json', ROOT), 'execution_dependencies': [io.evidence_ref(ROOT / 'scripts' / name, ROOT) for name in ('run_difficulty_gpu_v2.py', 'run_gh_smoke.py', 'review_tools.py')]})

    def prepare(self):
        rules = self.directory / 'rules.json'
        io.write_json(rules, {'attempts_per_task': 4})
        amendment = self.directory / 'amendment.json'
        io.write_json(amendment, {'account': _release_expand('${ALLOCATION}'), 'partition': 'gh', 'walltime_sec': 172800, 'training_prohibited': True})
        identity = {'measurement_rules': io.evidence_ref(rules, ROOT), 'scheduling_amendment': io.evidence_ref(amendment, ROOT), 'server_seed': 1234, 'walltime_sec': 172800, 'members': [], 'planned': []}
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
                for n in range(4 if code == 0 else 2):
                    io.write_json(directory / 'jobs' / directory.name / ('trial-' + str(n)) / 'result.json', {'id': directory.name + str(n), 'trial_name': 'trial-' + str(n), 'finished_at': '2026-11-09T00:00:00Z'})

            def poll(self):
                self.polls += 1
                if fail == 'server_dies' and self.polls == 1:
                    server.returncode = 9
                if fail == 'signal' and self.polls == 1:
                    raise runner.BatchInterrupted('simulated SIGTERM')
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
                process.returncode = -15

        def run(argv, **kwargs):
            if 'verify_container_cleanup.py' in ' '.join(argv):
                cleanups.append(argv)
                io.write_json(Path(argv[-1]), {'passed': True, 'checks': []})
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        self.starts, self.stops, self.cleanups = (starts, stops, cleanups)
        with patch.dict(os.environ, {'SLURM_JOB_ID': '123'}), patch.object(runner, 'allocation', return_value='test allocation'), patch.object(runner, 'environment', return_value={}), patch.object(runner, 'model_command', return_value=(['fake-vllm'], 'locked-model')), patch.object(runner, 'model_environment', return_value={'VLLM_CACHE_ROOT': str(self.directory / 'cache/vllm'), 'TRITON_CACHE_DIR': str(self.directory / 'cache/triton'), 'FLASHINFER_WORKSPACE_BASE': str(self.directory / 'cache/flashinfer')}), patch.object(runner, 'wait_ready', side_effect=RuntimeError('startup failure') if fail == 'startup' else None), patch.object(runner.socket, 'socket'), patch.object(runner.subprocess, 'Popen', side_effect=popen), patch.object(runner.subprocess, 'run', side_effect=run), patch.object(runner, 'stop', side_effect=stop), patch.object(runner.time, 'sleep'):
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
        self.assertEqual(io.read_json(self.directory / 'launch-result.json')['started_member_count'], 1)

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
if __name__ == '__main__':
    unittest.main()
