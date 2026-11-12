import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'validation/spider2/workday002/reconstruction.py'
spec = importlib.util.spec_from_file_location('workday_reconstruction', PATH)
rebuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


class WorkdayReconstructionTests(unittest.TestCase):
    def test_only_declared_initial_database_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'models').mkdir()
            (root / 'models/model.sql').write_text('select 1')
            (root / 'workday.duckdb').write_bytes(b'initial db')
            (root / 'target').mkdir()
            (root / 'target/compiled.sql').write_text('old output')
            self.assertEqual(set(rebuild.source_manifest(root)), {'models/model.sql'})
            (root / 'models/answer.duckdb').write_bytes(b'answer')
            with self.assertRaisesRegex(ValueError, 'Database outside'):
                rebuild.source_manifest(root)

    def test_source_symlink_cannot_smuggle_external_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'models').mkdir()
            (root / 'models/leak.sql').symlink_to('/tmp/hidden-answer.sql')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                rebuild.source_manifest(root)

    def test_table_match_retains_duplicates_and_key_value_association(self):
        first = {'columns': [{'name': 'id', 'type': 'BIGINT'}, {'name': 'value', 'type': 'VARCHAR'}],
                 'rows': [{'id': 9007199254740992, 'value': 'A'}, {'id': 9007199254740993, 'value': 'B'}]}
        second = copy.deepcopy(first)
        second['rows'].reverse()
        self.assertTrue(rebuild.exact_table_equal(first, second))
        second['rows'][0]['value'], second['rows'][1]['value'] = 'A', 'B'
        self.assertFalse(rebuild.exact_table_equal(first, second))
        second = copy.deepcopy(first)
        second['rows'].append(second['rows'][0])
        self.assertFalse(rebuild.exact_table_equal(first, second))

    def test_dbt_tests_cannot_stand_in_for_all_model_execution(self):
        manifest = {'metadata': {'invocation_id': 'one'}, 'nodes': {
            key: {'resource_type': 'model', 'config': {'enabled': True}} for key in ['model.a', 'model.b']}}
        result = {'metadata': {'invocation_id': 'one'}, 'args': {'which': 'run', 'select': [], 'exclude': []},
                  'results': [{'unique_id': key, 'status': 'success'} for key in manifest['nodes']]}
        self.assertEqual(rebuild.verify_full_run(result, manifest), ['model.a', 'model.b'])
        tests = copy.deepcopy(result)
        tests['args']['which'] = 'test'
        with self.assertRaisesRegex(ValueError, 'unselected dbt run'):
            rebuild.verify_full_run(tests, manifest)
        result['results'] = [result['results'][0], result['results'][0]]
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            rebuild.verify_full_run(result, manifest)

    def test_run_manifest_must_be_from_same_invocation(self):
        manifest = {'metadata': {'invocation_id': 'old'}, 'nodes': {
            'model.a': {'resource_type': 'model', 'config': {'enabled': True}}}}
        result = {'metadata': {'invocation_id': 'new'}, 'args': {'which': 'run'},
                  'results': [{'unique_id': 'model.a', 'status': 'success'}]}
        with self.assertRaisesRegex(ValueError, 'different invocations'):
            rebuild.verify_full_run(result, manifest)
