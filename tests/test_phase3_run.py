"""Frozen pool selection and production task isolation regressions."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from prepare_phase3_run import selected_tasks, production_task


class Phase3RunTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'original'; version = root / 'variant'
        for directory in (source, version):
            io.write_json(directory / 'tests/test.json', {'native': True})
            (directory / 'instruction.md').write_text('revised' if directory == version else 'original')
            (directory / 'task.toml').write_text('[environment]\ndocker_image = "image.sif"\n')
        (root / 'image.sif').write_bytes(b'synthetic fixture, not a real image')
        row = {'task_key': 'ade__test', 'source': 'ade', 'split': 'train', 'eligible_for_sampling': True,
               'task_version': 'v2', 'run_path': str(version)}
        metadata = {'task_key': row['task_key'], 'task_version': 'v2', 'native_verifier_modified': False,
                    'production_task_hash': io.tree_hash(version)}
        io.write_json(root / 'metadata.json', metadata)
        row['task_version_metadata'] = io.evidence_ref(root / 'metadata.json', root)
        io.write_jsonl(root / 'candidates.jsonl', [row])
        io.write_json(root / 'freeze.json', {'candidates': io.evidence_ref(root / 'candidates.jsonl', root)})
        io.write_json(root / 'pointer.json', {'selection': io.evidence_ref(root / 'freeze.json', root)})
        return row, source, version

    def test_only_frozen_eligible_train_tasks_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, _, _ = self.fixture(root)
            selected, _ = selected_tasks(root, root / 'pointer.json', 'ade', ['ade__test'])
            self.assertEqual(selected, [row])
            for changes in ({'split': 'test'}, {'eligible_for_sampling': False}, {'source': 'spider2'}):
                changed = {**row, **changes}; io.write_jsonl(root / 'candidates.jsonl', [changed])
                io.write_json(root / 'freeze.json', {'candidates': io.evidence_ref(root / 'candidates.jsonl', root)})
                io.write_json(root / 'pointer.json', {'selection': io.evidence_ref(root / 'freeze.json', root)})
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    selected_tasks(root, root / 'pointer.json', 'ade', ['ade__test'])

    def test_modified_freeze_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            io.write_jsonl(root / 'candidates.jsonl', [{'task_key': 'forged'}])
            with self.assertRaisesRegex(ValueError, 'candidates changed'):
                selected_tasks(root, root / 'pointer.json', 'ade', ['ade__test'])

    def test_production_copy_preserves_native_verifier_and_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, source, version = self.fixture(root)
            image = io.file_hash(root / 'image.sif')
            path, facts = production_task(root, row, source, image)
            self.assertEqual(path, version)
            self.assertEqual(facts['task_hash'], io.tree_hash(version))
            with self.assertRaisesRegex(ValueError, 'image differs'):
                production_task(root, row, source, 'wrong-image')
            io.write_json(version / 'tests/test.json', {'native': False})
            metadata = io.read_json(root / 'metadata.json'); metadata['production_task_hash'] = io.tree_hash(version)
            io.write_json(root / 'metadata.json', metadata); row['task_version_metadata'] = io.evidence_ref(root / 'metadata.json', root)
            with self.assertRaisesRegex(ValueError, 'changed native tests'):
                production_task(root, row, source, image)

    def test_validation_payload_never_becomes_a_production_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, source, version = self.fixture(root)
            (version / 'audit-rebuild').mkdir(); (version / 'audit-rebuild/answer.sql').write_text('hidden')
            metadata = io.read_json(root / 'metadata.json'); metadata['production_task_hash'] = io.tree_hash(version)
            io.write_json(root / 'metadata.json', metadata); row['task_version_metadata'] = io.evidence_ref(root / 'metadata.json', root)
            with self.assertRaisesRegex(ValueError, 'Validation-only payload'):
                production_task(root, row, source, io.file_hash(root / 'image.sif'))


if __name__ == '__main__':
    unittest.main()
