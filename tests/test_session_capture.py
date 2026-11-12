"""Capture regressions: preserved native failures and same-submission evidence."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'validation/deb/dbt-session-attribution/audit_v2'


def load(name):
    spec = importlib.util.spec_from_file_location('session_capture_test_' + name, HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


checker = load('checker')
install = load('install_capture')


class SessionCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = {'sessions': [
            {'session_id': 'touch', 'customer_id': '9007199254740993', 'session_start': '2020-01-01T00:00:00',
             'referrer': None, 'utm_source': None, 'utm_medium': None, 'device_type': 'desktop',
             'duration_seconds': 5, 'page_views': 2, 'is_converted': False, 'order_id': None},
            {'session_id': 'conversion', 'customer_id': '9007199254740993', 'session_start': '2020-01-02T00:00:00',
             'referrer': None, 'utm_source': None, 'utm_medium': None, 'device_type': 'desktop',
             'duration_seconds': 0, 'page_views': 0, 'is_converted': True, 'order_id': 'order'}],
            'sales': [{'order_id': 'order', 'total_amount': '10.00'}]}
        self.output = {'columns': ['channel', 'total_conversions', 'total_revenue'],
                       'rows': [{'channel': 'Direct', 'total_conversions': 1, 'total_revenue': 10}],
                       'relation_type': 'BASE TABLE'}

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + '\n')
        return {'path': str(path.relative_to(self.root)), 'sha256': checker.sha(path)}

    def capture(self, phase, output=True):
        out = self.root / phase; files = {}
        for name, obj in [('inputs.json', self.inputs), ('output.json', self.output if output else None)]:
            r = self.save(out / name, obj); files[name] = {'sha256': r['sha256']}
        projects = {}
        for label, path in checker.collector.PROJECTS.items():
            inventory = {}
            for name in checker.collector.COPIED_FILES:
                target = 'projects/' + label + '/' + name
                if label == 'native' and name == 'models/marts/attribution_report.sql' and output:
                    r = self.save(out / target, 'fixture model bytes')
                    files[target] = {'sha256': r['sha256']}
                    inventory[name] = {'sha256': r['sha256'], 'executable': False}
                else: files[target] = {'absent': True}
            projects[label] = {'path': path, 'present': label == 'native', 'source_inventory': inventory}
        db = {str(checker.collector.DATABASE): 'unchanged-database', str(checker.collector.DATABASE) + '.wal': None}
        value = dict(schema_version=1, phase=phase, errors=[], read_only_database=True, dbt_invoked=False,
                     arbitrary_submission_sql_executed=False, collector_sha256=checker.sha(HERE / 'collect.py'),
                     database_files_before=db, database_files_after=deepcopy(db), projects=projects,
                     files=files, relation_type='BASE TABLE' if output else None)
        return self.save(out / 'snapshot.json', value)

    def request(self, output=True):
        return self.save(self.root / 'request.json', dict(schema_version=2, task_key=checker.TASK,
            data_scope='additional_input_fixture', workflow=None,
            before=self.capture('before-native', output), after=self.capture('after-native', output)))

    def score_fixture(self):
        # This explicit in-test registry admits only this hand-computable fixture.
        original = checker.read
        policy = {'input_profiles': {checker.sha(self.root / 'before-native/inputs.json'):
                  {'id': 'unit-fixture', 'data_scope': 'additional_input_fixture'}}}
        def read(path):
            return policy if Path(path).name == 'input-policy.json' else original(path)
        with patch.object(checker, 'read', side_effect=read):
            return checker.score(self.root, self.root / 'request.json')

    def test_correct_data_does_not_invent_workflow(self):
        self.request(); result = self.score_fixture()
        self.assertEqual(result['data']['status'], 'pass')
        self.assertEqual(result['status'], 'unknown'); self.assertIsNone(result['audit_reward'])
        with self.assertRaisesRegex(ValueError, 'Unregistered'):
            checker.score(self.root, self.root / 'request.json')

    def test_missing_output_is_a_scored_failure_only_after_valid_capture(self):
        self.request(False); result = self.score_fixture()
        self.assertEqual(result['status'], 'fail'); self.assertEqual(result['audit_reward'], 0)
        self.assertTrue(result['missing_output'])
        self.assertEqual(result['data']['issues'], [{'kind': 'missing_required_relation'}])

    def test_export_tampering_rejected(self):
        request = checker.read(self.root / self.request()['path'])
        (self.root / 'before-native/output.json').write_text('null\n')
        with self.assertRaisesRegex(ValueError, 'Captured export changed'):
            checker.load_capture(self.root, request['before'], 'before-native')

    def test_collector_failure_is_error_not_zero_reward(self):
        request = checker.read(self.root / self.request(False)['path'])
        path = self.root / request['before']['path']; value = checker.read(path)
        value['errors'] = [{'type': 'ImportError', 'message': 'duckdb absent'}]
        request['before'] = self.save(path, value); self.save(self.root / 'request.json', request)
        with self.assertRaisesRegex(ValueError, 'Invalid/failed'):
            self.score_fixture()

    def test_native_database_change_rejected(self):
        request = checker.read(self.root / self.request()['path'])
        path = self.root / request['after']['path']; value = checker.read(path)
        for name in ['database_files_before', 'database_files_after']:
            value[name][str(checker.collector.DATABASE)] = 'different-database'
        request['after'] = self.save(path, value); self.save(self.root / 'request.json', request)
        with self.assertRaisesRegex(ValueError, 'changed the submission'): self.score_fixture()

    def test_incomplete_inventory_and_false_absence_rejected(self):
        request = checker.read(self.root / self.request()['path'])
        path = self.root / request['before']['path']; value = checker.read(path)
        value['files'].pop('projects/instruction/logs/dbt.log')
        reference = self.save(path, value)
        with self.assertRaisesRegex(ValueError, 'Incomplete capture'): checker.load_capture(self.root, reference, 'before-native')
        value['files']['projects/instruction/logs/dbt.log'] = {'absent': True}
        self.save(path.parent / 'projects/instruction/logs/dbt.log', 'unattested artifact')
        reference = self.save(path, value)
        with self.assertRaisesRegex(ValueError, 'Invalid absence'): checker.load_capture(self.root, reference, 'before-native')

    def test_source_escape_rejected(self):
        project = self.root / 'project'; project.mkdir()
        outside = self.root / 'other'; outside.mkdir()
        (project / 'models').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink/path'):
            checker.collector.safe_source(project / 'models/file.sql', project)

    def test_transient_materialization_is_local_and_preserves_original(self):
        transfer = load('transfer')
        original = self.root / 'retained-artifacts'; original.mkdir()
        (original / 'model.sql').write_text('immutable source')
        with transfer.local_artifact_alias(original) as alias:
            self.assertEqual(alias.parent.parent, Path('/tmp'))
            self.assertEqual(alias.resolve(), original)
            self.assertEqual((alias / 'model.sql').read_text(), 'immutable source')
            staging = alias.parent / '.verifier-artifacts-test'; staging.mkdir()
            (staging / 'model.sql').write_text('independent sanitized copy')
            parent = alias.parent
        self.assertFalse(parent.exists())
        self.assertEqual((original / 'model.sql').read_text(), 'immutable source')

    def test_collector_exit_failure_preserves_native_reward_and_exit(self):
        for native_exit in [0, 13]:
            task = self.root / str(native_exit); tests = task / 'tests'; tests.mkdir(parents=True)
            original = 'printf "native-result\\n" > "' + str(task / 'reward.txt') + '"\nexit ' + str(native_exit) + '\n'
            (tests / 'test.sh').write_text(original)
            (tests / 'grader.py').write_text('native grader bytes')
            install.install(task)
            self.assertEqual((tests / 'audit-original-test.sh').read_text(), original)
            self.assertEqual((tests / 'grader.py').read_text(), 'native grader bytes')
            binary = task / 'bin'; binary.mkdir()
            fake = binary / 'python3'; fake.write_text('#!/bin/sh\nexit 37\n'); fake.chmod(0o755)
            result = subprocess.run(['bash', str(tests / 'test.sh')], env={**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH']}, capture_output=True)
            self.assertEqual(result.returncode, native_exit)
            self.assertEqual((task / 'reward.txt').read_text(), 'native-result\n')
            with self.assertRaisesRegex(ValueError, 'already installed'): install.install(task)


if __name__ == '__main__': unittest.main()
