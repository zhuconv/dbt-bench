import copy
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / 'validation/spider2/workday002/audit_v2'


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, BASE / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


audit = module('workday_capture_checker', 'checker.py')
installer = module('workday_capture_installer', 'install_capture.py')


class WorkdayCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.here = self.root / 'code'
        self.here.mkdir()
        (self.here / 'collect.py').write_bytes((BASE / 'collect.py').read_bytes())
        tables = ['job_profile', 'job_family_job_profile', 'job_family',
                  'job_family_job_family_group', 'job_family_group']
        inputs = {'workday_' + t + '_data': {'rows': []} for t in tables}
        inputs['workday_job_profile_data']['rows'] = [dict(
            id='9007199254740993', _fivetran_deleted=False,
            job_profile_code='P', title='Title', private_title=None,
            summary=None, description=None)]
        rows = audit.expected_rows(inputs)
        self.output = {'columns': [{'name': k, 'type': 'VARCHAR'} for k in rows[0]], 'rows': rows}
        self.snapshots = {}
        for phase in ['before-native', 'after-native']:
            folder = self.root / phase
            folder.mkdir()
            files = {}
            for name, value in [('inputs.json', inputs), ('output.json', self.output)]:
                self.write(folder / name, value)
                files[name] = {'sha256': audit.sha(folder / name)}
            for name in ['target/manifest.json', 'target/run_results.json', 'logs/dbt.log']:
                files[name] = {'absent': True}
            self.snapshots[phase] = dict(schema_version=1, phase=phase, errors=[],
                read_only_database=True, dbt_invoked=False,
                collector_sha256=audit.sha(self.here / 'collect.py'),
                database_sha256_before='a' * 64, database_sha256_after='a' * 64,
                project_files={}, files=files, relation_type='BASE TABLE')
        self.write(self.here / 'input-policy.json', {'input_profiles': {
            audit.sha(self.root / 'before-native/inputs.json'): {'id': 'synthetic', 'data_scope': 'additional_input_fixture'}},
            'required_model_ids': ['model.workday.target']})
        self.patcher = patch.object(audit, 'HERE', self.here)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.refresh()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + '\n')

    def ref(self, path):
        return {'path': path.relative_to(self.root).as_posix(), 'sha256': audit.sha(path)}

    def refresh(self):
        for phase, snapshot in self.snapshots.items():
            self.write(self.root / phase / 'snapshot.json', snapshot)
        self.manifest = self.root / 'request.json'
        self.write(self.manifest, dict(schema_version=2, task_key=audit.TASK,
            data_scope='additional_input_fixture', workflow=None,
            before=self.ref(self.root / 'before-native/snapshot.json'),
            after=self.ref(self.root / 'after-native/snapshot.json')))

    def score(self):
        return audit.score(self.root, self.manifest)

    def test_correct_data_without_workflow_stays_unknown(self):
        result = self.score()
        self.assertEqual(result['data']['status'], 'pass')
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['audit_reward'])

    def test_missing_output_is_wrong_not_collection_error(self):
        for phase, snapshot in self.snapshots.items():
            self.write(self.root / phase / 'output.json', None)
            snapshot['files']['output.json']['sha256'] = audit.sha(self.root / phase / 'output.json')
            snapshot['relation_type'] = None
        self.refresh()
        self.assertEqual(self.score()['audit_reward'], 0)
        self.assertTrue(self.score()['missing_output'])

    def test_collection_failure_is_error_not_semantic_rejection(self):
        self.snapshots['before-native']['errors'] = [{'message': 'Cannot read database'}]
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'failed collection'):
            self.score()
        result_path = self.root / 'error-result.json'
        run = subprocess.run([sys.executable, str(BASE / 'checker.py'), '--root', str(self.root),
                              '--manifest', str(self.manifest), '--output', str(result_path)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        result = json.loads(result_path.read_text())
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['audit_reward'])

    def test_v2_keys_large_ids_row_associations_and_duplicates_remain_exact(self):
        rows = copy.deepcopy(self.output['rows'])
        other = dict(rows[0], job_profile_id='9007199254740992', job_title='Other')
        rows.append(other)
        output = dict(self.output, rows=copy.deepcopy(rows[::-1]))
        self.assertEqual(audit.compare_rows(rows, output)['status'], 'pass')
        output['rows'][0]['job_title'], output['rows'][1]['job_title'] = 'Title', 'Other'
        self.assertEqual(audit.compare_rows(rows, output)['status'], 'fail')
        output['rows'] = [rows[0], rows[0]]
        self.assertEqual({x['kind'] for x in audit.compare_rows(rows, output)['issues']},
                         {'duplicate_key', 'missing_row'})
        output['rows'] = [dict(rows[0], job_profile_id=9007199254740993), rows[1]]
        self.assertEqual(audit.compare_rows(rows, output)['status'], 'fail')

    def test_uninventoried_export_cannot_be_read_as_trusted(self):
        for snapshot in self.snapshots.values():
            del snapshot['files']['output.json']
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'capture inventory'):
            self.score()

    def test_changed_export_and_changed_submission_are_rejected(self):
        self.write(self.root / 'before-native/output.json', {'rows': []})
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            self.score()
        self.write(self.root / 'before-native/output.json', self.output)
        self.snapshots['after-native']['database_sha256_before'] = 'b' * 64
        self.snapshots['after-native']['database_sha256_after'] = 'b' * 64
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'changed the submission'):
            self.score()

    def test_absence_attestation_and_relation_type_must_agree(self):
        self.snapshots['before-native']['relation_type'] = None
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'absence.*disagree'):
            self.score()

    def test_collector_version_and_database_write_are_rejected(self):
        for field, value in [('collector_sha256', 'b' * 64),
                             ('database_sha256_after', 'b' * 64), ('dbt_invoked', True)]:
            original = copy.deepcopy(self.snapshots['before-native'])
            self.snapshots['before-native'][field] = value
            self.refresh()
            with self.assertRaises(ValueError):
                self.score()
            self.snapshots['before-native'] = original

    def test_views_fail_even_when_data_correct_and_workflow_unknown(self):
        for snapshot in self.snapshots.values():
            snapshot['relation_type'] = 'VIEW'
        self.refresh()
        self.assertEqual(self.score()['audit_reward'], 0)
        self.assertIn('target_is_not_table', self.score()['workflow']['issues'])

    def test_wrapper_preserves_native_exit_and_reward_when_collector_errors(self):
        tests = self.root / 'task/tests'
        tests.mkdir(parents=True)
        original = b'#!/bin/bash\nprintf 1 > reward.txt\nexit 7\n'
        (tests / 'test.sh').write_bytes(original)
        installer.install(tests.parent)
        self.assertEqual((tests / 'audit-original-test.sh').read_bytes(), original)
        # Substitute only the isolated test's absolute container paths.
        script = (tests / 'test.sh').read_text().replace('/workspace', shlex.quote(str(self.root)))
        script = script.replace('/tests/', str(tests) + '/')
        collector = tests / 'workday-audit-collect.py'
        collector.write_text('from pathlib import Path\nimport sys\n'
                             "with Path('phases.txt').open('a') as f: f.write(sys.argv[1]+'\\n')\n"
                             'raise SystemExit(19)\n')
        run = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
        self.assertEqual(run.returncode, 7)
        self.assertEqual((self.root / 'reward.txt').read_text(), '1')
        self.assertEqual((self.root / 'phases.txt').read_text().splitlines(), ['before-native', 'after-native'])


if __name__ == '__main__':
    unittest.main()
