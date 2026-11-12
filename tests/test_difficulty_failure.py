from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from dbt_bench import io
from difficulty_collect_v2 import collect
from difficulty_failure import validate_failure


class DifficultyDirectFailureTest(unittest.TestCase):
    def fixture(self, root):
        trial = root/'trial'; destination = trial/'artifacts/difficulty-submission'
        collect({'task_key': 'task', 'project_root': str(root/'absent-project'),
                 'database_paths': [str(root/'absent.db')]}, destination)
        io.write_json(trial/'artifacts/manifest.json', [{
            'source': '/tmp/dbt-bench-difficulty-submission', 'destination': 'artifacts/difficulty-submission',
            'status': 'ok', 'type': 'directory'}])
        io.write_json(trial/'result.json', {'agent_execution': {'finished_at': '2020-01-01T00:00:00+00:00'},
                                         'verifier': {'started_at': '2030-01-01T00:00:00+00:00'}})
        score = {'task_key': 'task', 'reward': 0, 'status': 'scored'}
        io.write_json(root/'score.json', score)
        # This is a deliberately synthetic contract fixture, not a task certificate.
        io.write_json(root/'contract.json', {'required': ['delivered_table'], 'fixture_only': True})
        io.write_json(root/'checker.json', {'fixture_only': True})
        capture = io.evidence_ref(destination/'capture.json', root)
        receipt = {'evidence_kind': 'direct_contract_failure', 'rebuild_result': None,
            'task_key': 'task', 'task_version': 'v1', 'original_result': io.evidence_ref(trial/'result.json', root),
            'qualified_review': io.evidence_ref(root/'contract.json', root),
            'checker': io.evidence_ref(root/'checker.json', root), 'audit_result': io.evidence_ref(root/'score.json', root)}
        proof = {**{k: receipt[k] for k in ('task_key','task_version','original_result','qualified_review','checker')},
            'schema_version': 1, 'evaluation_kind': 'direct_contract_failure', 'capture': capture,
            'checker_result': receipt['audit_result'], 'reward': 0, 'status': 'scored',
            'rule_ids': ['R-output'], 'rule_evidence': [receipt['qualified_review']],
            'observations': [capture], 'complete_capture': True, 'model_requests': 0}
        self.bind(root, receipt, proof)
        return receipt, proof, score

    def bind(self, root, receipt, proof):
        io.write_json(root/'failure.json', proof)
        receipt['failure_evaluation_ref'] = io.evidence_ref(root/'failure.json', root)

    def test_complete_absence_binds_failure_but_never_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); receipt, proof, score = self.fixture(root)
            validate_failure(receipt, score, root)
            with self.assertRaises(ValueError):
                validate_failure(receipt, {**score, 'reward': 1}, root)

    def test_requires_concrete_reviewed_rule_and_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); receipt, proof, score = self.fixture(root)
            for name in ('rule_ids', 'rule_evidence', 'observations'):
                changed = deepcopy(proof); changed[name] = []
                self.bind(root, receipt, changed)
                with self.assertRaises(ValueError):
                    validate_failure(receipt, score, root)

    def test_foreign_trial_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); receipt, proof, score = self.fixture(root)
            proof['capture'] = receipt['qualified_review']; self.bind(root, receipt, proof)
            with self.assertRaises(ValueError):
                validate_failure(receipt, score, root)

    def test_changed_source_evidence_is_not_a_valid_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); receipt, proof, score = self.fixture(root)
            io.write_json(root/'contract.json', {'changed': True})
            with self.assertRaises(ValueError):
                validate_failure(receipt, score, root)
