"""A qualified checker cannot turn a failing formal submission into a pass."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from phase4_publish import candidate_evaluation as evaluate_candidate, stage


def candidate_evaluation(path, expected, rebuilt, root):
    return evaluate_candidate(path, expected, rebuilt, root, qualified_overlay=io.read_json(root / 'qualified.json'))


class CandidateScoreTests(unittest.TestCase):
    def fixture(self, root, score=1):
        identity = {'task_key': 'synthetic', 'task_version': 'v1'}
        rebuilt = {'trials': []}
        rows = []
        for number in range(2):
            directory = root / ('trial-' + str(number))
            raw = directory / 'result.json'
            io.write_json(raw, {'native_reward': 1})
            rebuilt['trials'].append({'result': io.evidence_ref(raw, root)})
            for stage, part in [('agent-submission', 'agent/audit-submission'),
                                ('verifier-before', 'verifier/mechanical-before'),
                                ('verifier-after', 'verifier/mechanical-after')]:
                snapshot = directory / part / 'snapshot.json'
                io.write_json(snapshot, {'task_key': 'synthetic', 'stage': stage})
                ref = io.evidence_ref(snapshot, root)
                spec, audit = directory / stage / 'request.json', directory / stage / 'audit.json'
                io.write_json(spec, {'task_key': 'synthetic', 'candidate_snapshot': ref, 'candidate_stage': stage})
                io.write_json(audit, {'task_key': 'synthetic', 'reward': score, 'input_refs': {'candidate_snapshot': ref}})
                rows.append({'trial': directory.name, 'stage': stage, 'audit_reward': score,
                             'candidate_snapshot': ref, 'spec': io.evidence_ref(spec, root),
                             'audit_result': io.evidence_ref(audit, root)})
        review = {'task_key': 'synthetic', 'phase4_identity': identity, 'model_requests': 0,
                  'records': rows, 'candidate_pass': score == 1}
        (root / 'checker.py').write_text('synthetic checker fixture')
        qualified = {'checker_version': 'fixture-v1', 'code_refs': [io.evidence_ref(root / 'checker.py', root)]}
        io.write_json(root / 'qualified.json', qualified)
        review.update(qualified_overlay=io.evidence_ref(root / 'qualified.json', root), **qualified)
        path = root / 'review.json'; io.write_json(path, review)
        return path, review, identity, rebuilt

    def test_checker_qualification_does_not_override_actual_candidate_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for score, expected in [(0, 'fail'), (None, 'unknown'), (1, 'pass')]:
                path, review, identity, rebuilt = self.fixture(root, score)
                result = candidate_evaluation(path, identity, rebuilt, root)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['passed'], score == 1)
                if score != 1:
                    review['candidate_pass'] = True; io.write_json(path, review)
                    with self.assertRaisesRegex(ValueError, 'actual scores'):
                        candidate_evaluation(path, identity, rebuilt, root)

    def test_missing_duplicate_and_foreign_trial_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, review, identity, rebuilt = self.fixture(root)
            for kind in ('missing', 'duplicate', 'foreign'):
                candidate = deepcopy(review)
                if kind == 'missing': candidate['records'].pop()
                elif kind == 'duplicate': candidate['records'][-1] = candidate['records'][0]
                else: candidate['records'][0]['trial'] = 'unrelated'
                io.write_json(path, candidate)
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    candidate_evaluation(path, identity, rebuilt, root)

    def test_score_and_snapshot_cannot_be_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, review, identity, rebuilt = self.fixture(root)
            changed = deepcopy(review); changed['records'][0]['audit_reward'] = 0
            io.write_json(path, changed)
            with self.assertRaisesRegex(ValueError, 'actual formal submission'):
                candidate_evaluation(path, identity, rebuilt, root)
            changed = deepcopy(review)
            changed['records'][0]['candidate_snapshot'] = changed['records'][3]['candidate_snapshot']
            io.write_json(path, changed)
            with self.assertRaisesRegex(ValueError, 'different formal trial'):
                candidate_evaluation(path, identity, rebuilt, root)

    def test_evidence_drift_and_identity_drift_invalidate_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, review, identity, rebuilt = self.fixture(root)
            wrong_identity = {**identity, 'task_version': 'v2'}
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                candidate_evaluation(path, wrong_identity, rebuilt, root)
            audit = root / review['records'][0]['audit_result']['path']
            io.write_json(audit, {'reward': 0})
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                candidate_evaluation(path, identity, rebuilt, root)

    def test_missing_candidate_argument_cannot_admit_a_qualified_checker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, review, identity, rebuilt = self.fixture(root, 0)
            with patch('phase4_publish.verify_protected', return_value=0), \
                    patch('phase4_publish.precheck', return_value={'phase4_identity': identity}), \
                    patch('phase4_publish.reproduction', return_value=rebuilt):
                with self.assertRaisesRegex(ValueError, 'actual six formal candidate'):
                    stage(root, root / 'native.json', root / 'qualified.json', root / 'reconciliation.json', root)

    def test_checker_substitution_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, review, identity, rebuilt = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'reviewed checker'):
                evaluate_candidate(path, identity, rebuilt, root)
            review['checker_version'] = 'unreviewed'; io.write_json(path, review)
            with self.assertRaisesRegex(ValueError, 'differs from the reviewed checker'):
                candidate_evaluation(path, identity, rebuilt, root)

    def test_explicit_raw_fields_and_workflow_zero_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, review, identity, rebuilt = self.fixture(root)
            workflow = root / 'workflow.json'; io.write_json(workflow, {'reward': 0})
            for row in review['records']:
                row['audit_field_paths'] = {'reward': ['reward'], 'candidate_snapshot': ['input_refs', 'candidate_snapshot'], 'task_key': ['task_key']}
                row['workflow_result'] = io.evidence_ref(workflow, root)
                row['workflow_reward_path'] = ['reward']; row['audit_reward'] = 0
            review['candidate_pass'] = False; io.write_json(path, review)
            self.assertEqual(candidate_evaluation(path, identity, rebuilt, root)['status'], 'fail')
            review['records'][0]['audit_field_paths']['reward'] = ['nonexistent']; io.write_json(path, review)
            with self.assertRaisesRegex(ValueError, 'field is absent'):
                candidate_evaluation(path, identity, rebuilt, root)


if __name__ == '__main__':
    unittest.main()
