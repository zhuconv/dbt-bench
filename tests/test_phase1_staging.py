import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stage_phase1_reviews import validate_review, task_paused
from phase1_mechanical import MECHANICAL
from dbt_bench import io


class StagingTests(unittest.TestCase):
    def test_explicit_spider_resume_removes_only_pause_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pause = root / 'spider-pause-20261106.json'
            io.write_json(pause, {'pause_semantic_agents': True})
            self.assertTrue(task_paused('spider2__workday002', root))
            self.assertFalse(task_paused('deb__task', root))
            io.write_json(pause, {'pause_semantic_agents': False})
            self.assertFalse(task_paused('spider2__workday002', root))

    def test_missing_execution_and_changed_artifact_cannot_be_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'actual-log.txt'
            artifact.write_text('actual execution')
            check = {'status': 'gap', 'reason': 'Executed query; original data cannot distinguish the alternatives',
                     'evidence': [io.evidence_ref(artifact, root)]}
            value = {'task_key': 'one', 'target': 'native', 'base_identity': {'task': 'fixed'},
                     'checks': {name: copy.deepcopy(check) for name in MECHANICAL}}
            validate_review(value, 'one', {'task': 'fixed'}, root)
            with self.assertRaises(ValueError):
                validate_review(value, 'one', {'task': 'changed'}, root)
            for replacement in ({**check, 'status': 'pending'}, {**check, 'evidence': []}):
                changed = copy.deepcopy(value)
                changed['checks']['reference_repeat'] = replacement
                with self.assertRaises(ValueError):
                    validate_review(changed, 'one', {'task': 'fixed'}, root)
            artifact.write_text('different execution')
            with self.assertRaises(ValueError):
                validate_review(value, 'one', {'task': 'fixed'}, root)


if __name__ == '__main__':
    unittest.main()
