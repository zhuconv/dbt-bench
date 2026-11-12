"""Regression: source-only native tasks must not be graded as prebuilt tables."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
try:
    from dbt_bench import io
    import difficulty_deb_phase4_operations_v2 as adapter
except ImportError as exc:
    raise unittest.SkipTest('Locked runtime dependencies unavailable: ' + str(exc))


class SourceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trial = self.root / 'actual-rebuild'
        self.fresh = self.trial / 'agent/audit-submission/snapshot.json'
        self.fresh.parent.mkdir(parents=True)
        io.write_json(self.fresh, {'task_key': 'deb__dbt-customer-risk-scoring',
                                 'stage': 'agent-submission', 'outputs': ['fresh-correct-table']})
        self.original = self.root / 'original.json'
        io.write_json(self.original, {'outputs': [], 'scope': 'unchanged initial database'})
        self.request = {'task_key': 'deb__dbt-customer-risk-scoring',
                        'audit_dir': str(self.root / 'rebuild'), 'output_dir': str(self.root / 'score'),
                        'snapshot': io.evidence_ref(self.original, self.root),
                        'original_snapshot': io.evidence_ref(self.original, self.root),
                        'reconstruction_snapshot': io.evidence_ref(self.fresh, self.root),
                        'original_events': {'source_logs': ['actual-model-session']},
                        'probe': {'scope': 'original-input-identity'}}
        self.context = {'trial': self.trial}
        self.policy = {'scoring_basis': 'submitted_source_reconstruction'}

    def execute(self, request, evaluator):
        with patch.object(adapter, 'verify', return_value=self.context), \
                patch.object(adapter, 'policy', return_value=self.policy), \
                patch.object(adapter.previous, 'run', side_effect=evaluator), \
                patch.object(adapter.previous, 'save', return_value={'path': 'delivery.json', 'sha256': 'bound'}):
            return adapter.run(request, self.root)

    def test_source_only_uses_fresh_table_but_preserves_original_action_gap(self):
        def evaluator(request, root):
            self.assertEqual(request['snapshot'], self.request['reconstruction_snapshot'])
            self.assertEqual(request['original_events'], self.request['original_events'])
            return {'reward': None, 'status': 'unknown', 'original_action_gate': {'reward': None},
                    'table_reward': 1}
        original = io.evidence_ref
        with patch.object(io, 'evidence_ref', wraps=original) as evidence:
            # New adapter implementation references are outside this isolated
            # fixture root; these hashes are irrelevant to the routing control.
            evidence.side_effect = lambda path, root: ({'path': str(path), 'sha256': 'fixture-code'}
                if str(path).endswith(('difficulty_deb_phase4_operations_v2.py', 'difficulty_deb_operations_delivery.py'))
                else original(path, root))
            result = self.execute(self.request, evaluator)
        self.assertEqual(result['table_reward'], 1)
        self.assertIsNone(result['reward'])
        self.assertFalse(result['original_model_dbt_execution_inferred'])
        self.assertEqual(result['original_snapshot'], self.request['snapshot'])
        self.assertEqual(json.loads(self.original.read_text())['outputs'], [])

    def test_unbound_reconstruction_rejected(self):
        request = dict(self.request, reconstruction_snapshot={'path': 'other-trial/snapshot.json', 'sha256': 'wrong'})
        with self.assertRaisesRegex(ValueError, 'bound exact-source'):
            self.execute(request, lambda *_: self.fail('checker must not run'))

    def test_original_alias_cannot_be_relabelled(self):
        request = dict(self.request, original_snapshot=self.request['reconstruction_snapshot'])
        with self.assertRaisesRegex(ValueError, 'Original snapshot alias'):
            self.execute(request, lambda *_: self.fail('checker must not run'))


if __name__ == '__main__':
    unittest.main()
