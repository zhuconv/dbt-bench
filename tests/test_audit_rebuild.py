"""Trusted reconstruction must preserve input and record failures honestly."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_rebuild_driver import execute, install_submission, local


class RebuildTests(unittest.TestCase):
    def fixture(self, root):
        project = root / 'project'; project.mkdir()
        payload = root / 'payload'; payload.mkdir()
        data = b'select 42 as answer\n'; (payload / 'model.sql').write_bytes(data)
        spec = {'task_key': 'synthetic', 'project_root': str(project),
            'protected_paths': [str(project / 'original.duckdb')],
            'submission': {'files': [{'path': 'models/output.sql', 'payload_path': 'model.sql',
                          'sha256': hashlib.sha256(data).hexdigest()}], 'reset_paths': [], 'delete_paths': []},
            'capture_files': ['models/output.sql', 'target/manifest.json'], 'commands': [['dbt', 'run']]}
        (project / 'original.duckdb').write_bytes(b'untouched synthetic input')
        return project, payload, spec

    def test_all_payload_hashes_checked_before_install(self):
        with tempfile.TemporaryDirectory() as d:
            project, payload, spec = self.fixture(Path(d))
            spec['submission']['files'].append({'path': 'second.sql', 'payload_path': 'missing', 'sha256': 'bad'})
            with self.assertRaises(OSError): install_submission(spec, payload)
            self.assertFalse((project / 'models/output.sql').exists())

    def test_protected_input_cannot_be_reset(self):
        with tempfile.TemporaryDirectory() as d:
            project, payload, spec = self.fixture(Path(d))
            spec['submission']['reset_paths'] = ['original.duckdb']
            with self.assertRaises(ValueError): install_submission(spec, payload)
            self.assertEqual((project / 'original.duckdb').read_bytes(), b'untouched synthetic input')

    def test_symlink_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            (project / 'models').symlink_to(payload, target_is_directory=True)
            with self.assertRaises(ValueError): install_submission(spec, payload)
            with self.assertRaises(ValueError): local(project, '../escape')

    def test_failed_command_recorded_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            (project / 'target').mkdir(); (project / 'target/manifest.json').write_text('candidate supplied fake success')
            path = payload / 'request.json'; path.write_text(json.dumps(spec))
            def failed(command, **kwargs):
                self.assertFalse((project / 'target/manifest.json').exists())
                self.assertEqual(command, ['dbt', 'run'])
                return type('Process', (), {'returncode': 2})()
            with patch('audit_rebuild_driver.subprocess.run', side_effect=failed):
                report = execute(path, root / 'evidence')
            self.assertFalse(report['build_success'])
            self.assertEqual(report['runs'][0]['returncode'], 2)
            self.assertFalse(report['runs'][0]['files'][1]['exists'])

    def test_native_seed_then_build_workflow_is_supported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            spec['commands'] = [['dbt', 'seed'], ['dbt', 'run']]
            path = payload / 'request.json'; path.write_text(json.dumps(spec))
            with patch('audit_rebuild_driver.subprocess.run', return_value=type('Process', (), {'returncode': 0})()) as run:
                report = execute(path, root / 'evidence')
            self.assertTrue(report['build_success'])
            self.assertEqual([call.args[0] for call in run.call_args_list], [['dbt', 'seed'], ['dbt', 'run']])

    def test_real_test_subprocess_runs_after_build_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            binary = root / 'bin'; binary.mkdir(); dbt = binary / 'dbt'
            dbt.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\n'
                'p=Path("invocations");p.open("a").write(sys.argv[1]+"\\n")\n'
                'print("actual test assertion failure" if sys.argv[1]=="test" else "built")\n'
                'sys.exit(3 if sys.argv[1]=="test" else 0)\n')
            dbt.chmod(0o755)
            spec['commands'] = [['dbt', 'run'], ['dbt', 'test']]
            path = payload / 'request.json'; path.write_text(json.dumps(spec))
            with patch.dict(os.environ, {'PATH': str(binary) + os.pathsep + os.environ['PATH']}):
                report = execute(path, root / 'evidence')
            self.assertNotIn('error', report)
            self.assertFalse(report['build_success'])
            self.assertEqual((project / 'invocations').read_text(), 'run\ntest\n')
            self.assertEqual([r['returncode'] for r in report['runs']], [0, 3])
            self.assertIn('actual test assertion failure', (root / 'evidence/command-01.log').read_text())

    def test_unsupported_later_command_rejected_before_any_mutation_or_process(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            spec['commands'] = [['dbt', 'run'], ['sh', '-c', 'echo must-not-run']]
            path = payload / 'request.json'; path.write_text(json.dumps(spec))
            with patch('audit_rebuild_driver.subprocess.run') as run:
                report = execute(path, root / 'evidence')
            run.assert_not_called()
            self.assertFalse((project / 'models/output.sql').exists())
            self.assertFalse(report['build_success'])
            self.assertEqual(report['runs'], [])
            self.assertIn('declared dbt commands', report['error']['message'])

    def test_command_hook_captures_first_output_before_second_build_changes_it(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            binary = root / 'bin'; binary.mkdir()
            dbt = binary / 'dbt'
            dbt.write_text('#!' + sys.executable + '\nfrom pathlib import Path\np=Path("counter")\np.write_text(str(int(p.read_text())+1 if p.exists() else 1))\n')
            dbt.chmod(0o755)
            hook = payload / 'capture.py'
            hook.write_text('import sys\nfrom pathlib import Path\np=Path(sys.argv[2]);p.mkdir()\n(p/"value.txt").write_text(Path(sys.argv[1]).read_text())\n')
            spec['commands'] = [['dbt', 'run'], ['dbt', 'run']]
            spec['capture_hooks'] = [{'phase': 'after_command', 'command_index': 0,
                'payload_path': hook.name, 'sha256': hashlib.sha256(hook.read_bytes()).hexdigest(),
                'args': [str(project / 'counter'), '{output}']}]
            path = payload / 'request.json'; path.write_text(json.dumps(spec))
            with patch.dict(os.environ, {'PATH': str(binary) + os.pathsep + os.environ['PATH']}):
                report = execute(path, root / 'evidence')
            self.assertTrue(report['build_success'])
            self.assertEqual((project / 'counter').read_text(), '2')
            self.assertEqual((root / 'evidence/hook-after_command-00-command-00/value.txt').read_text(), '1')
            self.assertEqual(report['runs'][0]['capture_hooks'][0]['returncode'], 0)
            self.assertEqual(report['runs'][1]['capture_hooks'], [])

    def test_out_of_range_command_hook_fails_before_submission_install(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project, payload, spec = self.fixture(root)
            spec['capture_hooks'] = [{'phase': 'after_command', 'command_index': 1}]
            path = payload / 'request.json'; path.write_text(json.dumps(spec))
            report = execute(path, root / 'evidence')
            self.assertFalse(report['build_success'])
            self.assertFalse((project / 'models/output.sql').exists())
            self.assertIn('command index', report['error']['message'])


if __name__ == '__main__': unittest.main()
