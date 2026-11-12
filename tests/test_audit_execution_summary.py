from pathlib import Path
import tempfile
import unittest

from dbt_bench import io
from summarize_audit_execution import summarize


def batch(tmp_path, name='one', trial_id='trial', rewards=None, error=None):
    directory = tmp_path / name
    directory.mkdir()
    manifest = directory / 'manifest.json'
    io.write_json(manifest, {'kind': 'audit_clean_reconstruction', 'model_requests': 0,
                            'tasks': {'candidate': {'task_key': 'deb__example'}}})
    result = directory / 'result.json'
    io.write_json(result, {'id': trial_id, 'finished_at': 'done',
        'config': {'task': {'path': '/isolated/candidate'}},
        'verifier_result': {'rewards': rewards}, 'exception_info': None})
    execution = directory / 'execution.json'
    io.write_json(execution, {'task_key': 'deb__example', 'model_requests': 0,
                             'build_success': error is None, 'error': error})
    report = directory / 'reconciliation-v1.json'
    io.write_json(report, {'batch': name, 'complete': True, 'missing': {}, 'excess': {}, 'errors': [],
        'model_requests': 0, 'manifest': io.evidence_ref(manifest, tmp_path),
        'planned_trials': 1, 'observed_trials': 1, 'trials': [{
            'trial_id': trial_id, 'task_key': 'deb__example', 'label': 'candidate',
            'result': io.evidence_ref(result, tmp_path), 'execution': io.evidence_ref(execution, tmp_path),
            'build_success': error is None, 'native_rewards': rewards, 'exception_info': None}]})
    return report


class AuditExecutionSummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_native_success_does_not_hide_producer_failure(self):
        tmp_path = self.root
        error = {'type': 'OSError', 'message': 'target cleanup failed before dbt'}
        p = batch(tmp_path, rewards={'reward': 1.0, 'extra_metric': 0.25}, error=error)
        before = {f: f.read_bytes() for f in p.parent.iterdir()}
        result = summarize([p], tmp_path)
        assert result['trials'][0]['native_rewards'] == {'reward': 1.0, 'extra_metric': 0.25}
        assert result['trials'][0]['reconstruction_error'] == error
        assert result['harbor_exception_count'] == 0
        assert result['reconstruction_error_count'] == result['build_failure_count'] == 1
        assert not result['audit_certified']
        assert all(f.read_bytes() == data for f, data in before.items())


    def test_duplicate_trial_across_batches_is_rejected(self):
        tmp_path = self.root
        a = batch(tmp_path, 'one')
        b = batch(tmp_path, 'two')
        with self.assertRaisesRegex(ValueError, 'Duplicate or mismatched trial'):
            summarize([a, b], tmp_path)


    def test_changed_raw_reward_is_rejected(self):
        tmp_path = self.root
        p = batch(tmp_path, rewards={'reward': 0.0})
        raw = p.parent / 'result.json'
        changed = io.read_json(raw)
        changed['verifier_result']['rewards']['reward'] = 1.0
        io.write_json(raw, changed)
        with self.assertRaisesRegex(ValueError, 'Changed or unavailable'):
            summarize([p], tmp_path)


    def test_missing_reward_and_actual_nop_stay_separate(self):
        tmp_path = self.root
        p = batch(tmp_path, rewards=None)
        r = io.read_json(p)
        t = r['trials'][0]
        t.pop('execution')
        t.pop('build_success')
        t['build_skipped'] = 'Actual nop'
        io.write_json(p, r)
        result = summarize([p], tmp_path)
        assert result['trials'][0]['native_rewards'] is None
        assert result['trials'][0]['build_success'] is None
        assert result['trials'][0]['build_skipped'] == 'Actual nop'
        assert result['harbor_exception_count'] == result['build_failure_count'] == 0


    def test_incomplete_batch_cannot_disappear_from_denominator(self):
        tmp_path = self.root
        p = batch(tmp_path)
        r = io.read_json(p)
        r.update(complete=False, missing={'candidate': 1}, planned_trials=2)
        io.write_json(p, r)
        with self.assertRaisesRegex(ValueError, 'Only complete reconciled'):
            summarize([p], tmp_path)
