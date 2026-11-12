from pathlib import Path
import subprocess
import tempfile
import unittest

from dbt_bench.catalog import import_legacy, load_catalog
from dbt_bench.io import digest, evidence_ref, read_json, read_jsonl, write_json, write_jsonl
from tests.helpers import fixture_root


class CatalogTests(unittest.TestCase):
    def frozen_fixture(self, root):
        row = fixture_root(root)
        row.update(task_family='reviewed-group', workflow='add_models', project_family='fixture',
                   warehouse_family='unknown', legacy_split={'v3':'train'},
                   exposure_history=[{'version':'v3', 'split':'train', 'training_use':'unknown'}])
        fields = ('split', 'split_version', 'task_family', 'workflow', 'project_family',
                  'warehouse_family', 'legacy_split', 'exposure_history')
        write_json(root/'review-input.json', {'instruction_review':'Synthetic test fixture only'})
        split = {'version':row['split_version'], 'status':'reviewed_frozen',
                 'assignments':{row['task_key']:'train'}, 'groups':{'reviewed-group':[row['task_key']]}}
        write_json(root/'review.json', {'kind':'reviewed-family-split-v1', 'version':split['version'],
                   'candidate_ids_hash':digest([row['task_key']]), 'assignments':split['assignments'],
                   'groups':split['groups'], 'tasks':{row['task_key']:{k:row[k] for k in fields}},
                   'input_reviews':[evidence_ref(root/'review-input.json',root)]})
        split['review'] = evidence_ref(root/'review.json', root)
        split['hash'] = digest(split)
        row['family_review'] = {'status':'reviewed', 'evidence':split['review']}
        write_json(root/'catalog/splits.json', split)
        write_jsonl(root/'catalog/tasks.jsonl', [row])
        return row, split

    def test_frozen_label_without_review_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture_root(root)
            split = read_json(root/'catalog/splits.json')
            split['status'] = 'reviewed_frozen'
            split['hash'] = digest({k:v for k,v in split.items() if k!='hash'})
            write_json(root/'catalog/splits.json', split)
            with self.assertRaisesRegex(ValueError, 'review missing'):
                load_catalog(root)

    def test_frozen_metadata_and_history_cannot_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, _ = self.frozen_fixture(root)
            self.assertEqual(load_catalog(root)[0], row)
            for field, value in [('split_version','other-version'), ('task_family','other-family'),
                                 ('exposure_history',[]), ('workflow','unknown'), ('family_review','pending')]:
                with self.subTest(field=field):
                    write_jsonl(root/'catalog/tasks.jsonl', [{**row,field:value}])
                    with self.assertRaisesRegex(ValueError, 'metadata differs'):
                        load_catalog(root)

    def test_frozen_review_and_underlying_decisions_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, split = self.frozen_fixture(root)
            original = (root/'review.json').read_bytes()
            write_json(root/'review.json', {'kind':'replaced'})
            with self.assertRaisesRegex(ValueError, 'review missing or changed'):
                load_catalog(root)
            (root/'review.json').write_bytes(original)
            write_json(root/'review-input.json', {'instruction_review':'changed'})
            with self.assertRaisesRegex(ValueError, 'inputs missing or changed'):
                load_catalog(root)

    def test_rehashed_split_still_must_match_reviewed_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, split = self.frozen_fixture(root)
            row['split'] = 'test'; split['assignments'][row['task_key']] = 'test'
            split['hash'] = digest({k:v for k,v in split.items() if k!='hash'})
            write_json(root/'catalog/splits.json', split)
            write_jsonl(root/'catalog/tasks.jsonl', [row])
            with self.assertRaisesRegex(ValueError, 'differs from reviewed decisions'):
                load_catalog(root)

    def legacy(self, root):
        task = {"source": "deb", "task": "fixture", "reason": "legacy reason"}
        for name, rows in {"train_tasks_v3": [], "test_tasks_v3": [task],
                           "train_tasks_v2": [task], "test_tasks_v2": []}.items():
            write_json(root / "code/data" / f"{name}.json", rows)
        write_json(root / "code/harbor-cmp/excluded_tasks.json", {"deb__fixture": "Historical exclusion"})
        # A misleading historical certification must never enter the new ledger.
        write_json(root / "code/harbor-cmp/validated_tasks.json", {"deb__fixture": {"ok": True}})
        for command in (["git", "init", str(root)], ["git", "-C", str(root), "add", "."],
                        ["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-m", "fixture"]):
            subprocess.run(command, capture_output=True, check=True)

    def test_actual_input_differences_history_and_no_imported_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, old = Path(tmp) / "bench", Path(tmp) / "legacy"
            self.legacy(old)
            manifest = import_legacy(root, old)
            self.assertFalse(manifest["revision_matches_plan"])
            self.assertFalse(manifest["counts_match_plan"])
            self.assertEqual(manifest["candidate_count"], 1)
            row = load_catalog(root)[0]
            self.assertEqual(row["legacy_split"], {"v2": "train", "v3": "test"})
            self.assertEqual(row["pool_status"], "quarantined")
            self.assertNotIn("ok", row)
            before = (root / "catalog/tasks.jsonl").read_bytes()
            import_legacy(root, old)
            self.assertEqual(before, (root / "catalog/tasks.jsonl").read_bytes())

    def test_catalog_missing_and_duplicate_records_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = fixture_root(root)
            write_jsonl(root / "catalog/tasks.jsonl", [row, row])
            with self.assertRaises(ValueError):
                load_catalog(root)
            write_jsonl(root / "catalog/tasks.jsonl", [])
            with self.assertRaises(ValueError):
                load_catalog(root)

    def test_changed_legacy_input_does_not_overwrite_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, old = Path(tmp) / "bench", Path(tmp) / "legacy"
            self.legacy(old)
            import_legacy(root, old)
            before = (root / "catalog/tasks.jsonl").read_bytes()
            write_json(old / "code/data/test_tasks_v3.json", [])
            with self.assertRaises(ValueError):
                import_legacy(root, old)
            self.assertEqual(before, (root / "catalog/tasks.jsonl").read_bytes())
