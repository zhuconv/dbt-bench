import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'validation/spider2/workday002/semantics/independent.py'
spec = importlib.util.spec_from_file_location('workday_case_independent', PATH)
independent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(independent)


class WorkdaySemanticsTests(unittest.TestCase):
    def test_missing_row_proofs_ignore_enumeration_order_but_keep_identity_and_counts(self):
        check_spec = importlib.util.spec_from_file_location('workday_proof_comparison', PATH.with_name('proof_comparison.py'))
        module = importlib.util.module_from_spec(check_spec)
        check_spec.loader.exec_module(module)
        one = {'kind': 'missing_row', 'key': ['', 'p1']}
        two = {'kind': 'missing_row', 'key': ['', 'p2']}
        self.assertTrue(module.same_differences([one, two], [two, one]))
        self.assertFalse(module.same_differences([one, two], [one, one]))
        self.assertFalse(module.same_differences([one, two, two], [one, two]))

    def test_only_immutable_build_inputs_are_shared(self):
        copy_spec = importlib.util.spec_from_file_location('workday_case_copy', PATH.with_name('task_copy.py'))
        module = importlib.util.module_from_spec(copy_spec)
        copy_spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / 'source', Path(directory) / 'destination'
            for name in ['environment/project.sql', 'solution/solve.sh', 'tests/test.sh', 'instruction.md']:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original')
            module.copy_task(source, destination)
            self.assertEqual((source / 'environment/project.sql').stat().st_ino,
                             (destination / 'environment/project.sql').stat().st_ino)
            for name in ['solution/solve.sh', 'tests/test.sh', 'instruction.md']:
                self.assertNotEqual((source / name).stat().st_ino, (destination / name).stat().st_ino)
                (destination / name).write_text('case mutation')
                self.assertEqual((source / name).read_text(), 'original')

    def test_unordered_strings_keep_embedded_delimiter_and_duplicates(self):
        compare = independent.aggregate_matches
        self.assertTrue(compare('Beta, Ops, North', ['Ops, North', 'Beta']))
        self.assertTrue(compare('Ops, North, Beta', ['Ops, North', 'Beta']))
        self.assertFalse(compare('Ops, Beta, North', ['Ops, North', 'Beta']))
        self.assertFalse(compare('Beta, Beta, Ops, North', ['Ops, North', 'Beta']))
        self.assertFalse(compare(['Beta', 'Beta'], ['Beta']))

    def test_empty_and_null_are_only_relaxed_for_no_members(self):
        compare = independent.aggregate_matches
        for empty in [None, '', []]:
            self.assertTrue(compare(empty, []))
        self.assertTrue(compare('', ['']))
        self.assertFalse(compare(None, ['']))
        self.assertFalse(compare('', ['ALPHA']))
        self.assertTrue(compare(', ALPHA', ['', 'ALPHA']))
        self.assertTrue(compare('ALPHA, ', ['', 'ALPHA']))

    def test_row_association_duplicates_and_exact_core_nulls(self):
        expected = [dict(job_profile_id=str(i), source_relation='', job_profile_code=str(i),
                         job_title=title, private_title=None, job_summary=None, job_description=None,
                         **{name: [] for name in independent.AGGREGATES}) for i, title in [(1, 'A'), (2, 'B')]]
        output = {'columns': [{'name': name} for name in expected[0]], 'rows': copy.deepcopy(expected[::-1])}
        self.assertEqual(independent.assess(expected, output), [])
        output['rows'][0]['job_title'], output['rows'][1]['job_title'] = 'A', 'B'
        self.assertEqual(len(independent.assess(expected, output)), 2)
        output['rows'] = copy.deepcopy(expected)
        output['rows'].append(copy.deepcopy(expected[0]))
        self.assertEqual(independent.assess(expected, output)[0]['kind'], 'duplicate_key')
        output['rows'] = copy.deepcopy(expected)
        output['rows'][0]['private_title'] = ''
        self.assertEqual(independent.assess(expected, output)[0]['column'], 'private_title')

    def test_global_bridge_inactive_family_and_deleted_relationships(self):
        names = independent.INPUTS
        inputs = {name: {'rows': []} for name in names}
        def active(**fields):
            return {'_fivetran_deleted': False, **fields}
        inputs[names[0]]['rows'] = [active(id='p', job_profile_code='P', title='Title',
                                        private_title=None, summary=None, description=None)]
        inputs[names[1]]['rows'] = [active(job_profile_id='p', job_family_id='f')] * 2
        inputs[names[2]]['rows'] = [active(id='f', job_family_code='F', summary=None, inactive=True)]
        inputs[names[3]]['rows'] = [active(job_family_id='f', job_family_group_id='g'),
                                    active(job_family_id='f', job_family_group_id='deleted', _fivetran_deleted=True)]
        inputs[names[4]]['rows'] = [active(id=key, job_family_group_code=key.upper(), summary=None)
                                   for key in ['g', 'wrong', 'deleted']]
        inputs[names[5]]['rows'] = [active(job_family_id='f', job_family_group_id='wrong')]
        result = independent.recompute(inputs)
        self.assertEqual(result[0]['job_family_codes'], ['F'])
        self.assertEqual(result[0]['job_family_group_codes'], ['G'])
        inputs[names[2]]['rows'][0]['_fivetran_deleted'] = True
        self.assertEqual(independent.recompute(inputs)[0]['job_family_group_codes'], [])
