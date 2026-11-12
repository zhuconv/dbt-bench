from pathlib import Path
import tempfile
import unittest

from dbt_bench import io
from prepare_measured_production import production_task, select


class MeasuredProductionTest(unittest.TestCase):
    def fixture(self, root):
        base, variant = root / 'base', root / 'variant'
        for path, instruction in ((base, 'old instruction'), (variant, 'revised instruction')):
            io.write_json(path / 'tests/native.json', {'native': 'unchanged'})
            (path / 'instruction.md').write_text(instruction)
            (path / 'task.toml').write_text('[environment]\ndocker_image = "image.sif"\n')
        (root / 'image.sif').write_bytes(b'fixture not a container')
        metadata = {'task_key': 'deb__example', 'task_version': 'phase4-instruction-v1',
                    'native_verifier_modified': False, 'production_task_hash': io.tree_hash(variant)}
        io.write_json(root / 'metadata.json', metadata)
        review = {'task_key': 'deb__example', 'task_version': metadata['task_version'],
            'eligible_for_sampling': True, 'audit': {'conclusion': 'validated'}, 'native': {'conclusion': 'weak'},
            'phase4_identity': {'instruction_sha256': io.file_hash(variant / 'instruction.md'),
                'base_identity': {'backend_binding': {'image_sha256': io.file_hash(root / 'image.sif')}}}}
        io.write_json(root / 'review.json', review)
        row = {'task_key': 'deb__example', 'task_version': metadata['task_version'],
               'split': 'test', 'run_path': str(variant),
               'task_version_metadata': io.evidence_ref(root / 'metadata.json', root)}
        selected = {'task_key': row['task_key'], 'task_version': row['task_version'],
            'source': 'deb', 'qualification_phase': 4, 'eligible_for_measurement': True,
            'qualified_review': io.evidence_ref(root / 'review.json', root), 'frozen_pool_row': row}
        for name in ('phase3_pool', 'phase4_pool', 'split_rules'):
            io.write_json(root / (name + '.json'), {})
        io.write_json(root / 'configs/difficulty-v1/measurement-rules.json', {'training_prohibited': True})
        pool = {'phase4_review_complete': True, 'training_prohibited': True,
            'measurement_rules': io.evidence_ref(root / 'configs/difficulty-v1/measurement-rules.json', root),
            **{name: io.evidence_ref(root / (name + '.json'), root) for name in ('phase3_pool', 'phase4_pool', 'split_rules')},
            'tasks': [selected]}
        io.write_json(root / 'pool.json', pool)
        return base, variant, selected, review

    def test_test_side_is_explicitly_allowed_only_as_qualified_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, variant, selected, review = self.fixture(root)
            actual, actual_review = select(root, root / 'pool.json', 'deb', 'deb__example')
            self.assertEqual(actual, selected)
            self.assertEqual(actual['frozen_pool_row']['split'], 'test')
            self.assertEqual(production_task(root, actual, actual_review, base)[0], variant)

    def test_incomplete_phase4_cannot_be_presented_as_final_population(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.fixture(root)
            pool = io.read_json(root / 'pool.json'); pool['phase4_review_complete'] = False
            io.write_json(root / 'pool.json', pool)
            with self.assertRaises(ValueError):
                select(root, root / 'pool.json', 'deb', 'deb__example')

    def test_no_silent_fallback_to_old_instruction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, variant, selected, review = self.fixture(root)
            (variant / 'instruction.md').write_text('old instruction')
            metadata = io.read_json(root / 'metadata.json'); metadata['production_task_hash'] = io.tree_hash(variant)
            io.write_json(root / 'metadata.json', metadata)
            selected['frozen_pool_row']['task_version_metadata'] = io.evidence_ref(root / 'metadata.json', root)
            with self.assertRaisesRegex(ValueError, 'instruction version'):
                production_task(root, selected, review, base)

    def test_original_task_can_have_explicit_run_path_without_variant_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, _, selected, review = self.fixture(root)
            selected['frozen_pool_row'].update(run_path=str(base), task_version_metadata=None)
            review['phase4_identity']['instruction_sha256'] = io.file_hash(base / 'instruction.md')
            self.assertEqual(production_task(root, selected, review, base)[0], base)

    def test_validation_source_never_leaks_into_model_task(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, variant, selected, review = self.fixture(root)
            (variant / 'audit-rebuild').mkdir()
            (variant / 'audit-rebuild/answer.sql').write_text('secret reference')
            metadata = io.read_json(root / 'metadata.json'); metadata['production_task_hash'] = io.tree_hash(variant)
            io.write_json(root / 'metadata.json', metadata)
            selected['frozen_pool_row']['task_version_metadata'] = io.evidence_ref(root / 'metadata.json', root)
            with self.assertRaisesRegex(ValueError, 'Validation payload'):
                production_task(root, selected, review, base)
