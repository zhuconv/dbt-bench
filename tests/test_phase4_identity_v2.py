"""Exact addition, unrelated drift, and transitive-import regressions."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
import phase4_review_identity_v2 as compat


class IdentityCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.old = {'task': 'task-hash', 'environment_data': 'data-hash',
                    'verifier': 'verifier-hash', 'contract': 'contract-hash',
                    'backend_binding': {'image_sha256': 'image-hash'},
                    'implementation': {'validation.py': 'checker-hash', 'io.py': 'io-hash'}}
        self.current = deepcopy(self.old)
        self.current['implementation']['report_v6.py'] = compat.REPORT_SHA256

    def test_only_exact_addition_and_no_mutation(self):
        before = deepcopy((self.current, self.old))
        result = compat.compatible_identity(self.current, self.old, ROOT)
        self.assertTrue(result['all_other_identity_fields_exact'])
        self.assertEqual((self.current, self.old), before)
        self.assertEqual(result['allowed_addition']['path'], compat.REPORT_PATH)

    def test_other_fields_rejected(self):
        for key in ('task', 'environment_data', 'verifier', 'contract', 'backend_binding'):
            with self.subTest(key=key):
                current = deepcopy(self.current); current[key] = 'changed'
                with self.assertRaises(ValueError):
                    compat.compatible_identity(current, self.old, ROOT)

    def test_every_other_implementation_hash_required(self):
        for mutation in ('change', 'remove', 'add'):
            with self.subTest(mutation=mutation):
                current = deepcopy(self.current)
                if mutation == 'change': current['implementation']['io.py'] = 'changed'
                if mutation == 'remove': del current['implementation']['io.py']
                if mutation == 'add': current['implementation']['report_v7.py'] = compat.REPORT_SHA256
                with self.assertRaises(ValueError):
                    compat.compatible_identity(current, self.old, ROOT)

    def test_wrong_name_or_hash_rejected(self):
        for mutation in ('wrong_name', 'wrong_hash', 'missing', 'historical_present'):
            current, old = deepcopy(self.current), deepcopy(self.old)
            if mutation == 'wrong_name': current['implementation']['other.py'] = current['implementation'].pop('report_v6.py')
            if mutation == 'wrong_hash': current['implementation']['report_v6.py'] = 'changed'
            if mutation == 'missing': del current['implementation']['report_v6.py']
            if mutation == 'historical_present': old['implementation']['report_v6.py'] = compat.REPORT_SHA256
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compat.compatible_identity(current, old, ROOT)

    def test_actual_file_hash_must_match(self):
        with patch.object(compat.io, 'evidence_ok', return_value=False), self.assertRaises(ValueError):
            compat.compatible_identity(self.current, self.old, ROOT)

    def test_import_guard_blocks_dynamic_import(self):
        # Other test modules may have loaded reporting; restore their state.
        prior = sys.modules.pop(compat.REPORT_MODULE, None)
        try:
            before = list(sys.meta_path)
            with compat.without_reporting_imports(), self.assertRaises(ValueError):
                importlib.import_module(compat.REPORT_MODULE)
            self.assertEqual(sys.meta_path, before)
            self.assertNotIn(compat.REPORT_MODULE, sys.modules)
        finally:
            if prior is not None: sys.modules[compat.REPORT_MODULE] = prior

    def test_preloaded_reporting_rejected(self):
        with patch.dict(sys.modules, {compat.REPORT_MODULE: object()}), self.assertRaises(ValueError):
            with compat.without_reporting_imports(): pass

    def test_stage_reuses_original_without_mutation(self):
        before = compat.original_publish.stage.__globals__['precheck']
        prior = sys.modules.pop(compat.REPORT_MODULE, None)
        try:
            with patch.object(compat.original_publish, 'verify_protected', side_effect=RuntimeError('reached original stage')):
                with self.assertRaisesRegex(RuntimeError, 'reached original stage'):
                    compat.stage(ROOT, None, None, None)
            self.assertIs(compat.original_publish.stage.__globals__['precheck'], before)
        finally:
            if prior is not None: sys.modules[compat.REPORT_MODULE] = prior


if __name__ == '__main__':
    unittest.main()
