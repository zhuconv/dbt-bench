from pathlib import Path
import tempfile
import unittest

from dbt_bench import io
from reconcile_difficulty_v2 import verify_adapter_dependencies


class DifficultyDependenciesTest(unittest.TestCase):
    def test_helper_change_invalidates_prior_actual_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('adapter.py', 'helper.py', 'checker.py', 'image.sif'):
                (root / name).write_text('original ' + name)
            deps = {'code_refs': [io.evidence_ref(root / 'adapter.py', root),
                                  io.evidence_ref(root / 'helper.py', root)],
                    'checker_dependencies': [io.evidence_ref(root / 'checker.py', root)],
                    'image': io.evidence_ref(root / 'image.sif', root)}
            io.write_json(root / 'dependencies.json', deps)
            receipt = {'task_key': 'deb__test', 'adapter_dependencies': io.evidence_ref(root / 'dependencies.json', root)}
            verify_adapter_dependencies(receipt, root)
            (root / 'helper.py').write_text('changed shared semantic helper')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                verify_adapter_dependencies(receipt, root)

    def test_missing_deb_dependency_binding_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, 'dependency manifest'):
            verify_adapter_dependencies({'task_key': 'deb__test'})
