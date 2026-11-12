import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

PATH = Path(__file__).resolve().parents[1] / 'validation/spider2/workday002/audit_v1/checker.py'
spec = importlib.util.spec_from_file_location('workday_audit_checker', PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture():
    def row(**fields):
        return dict(_fivetran_deleted=False, **fields)
    return {
        'workday_job_profile_data': {'rows': [row(id='9007199254740993', job_profile_code='P', title='Job',
                                                  private_title=None, summary=None, description='Description')]},
        'workday_job_family_job_profile_data': {'rows': [row(job_profile_id='9007199254740993', job_family_id='f')] * 2},
        'workday_job_family_data': {'rows': [row(id='f', job_family_code='F', summary='Ops, North', inactive=True)]},
        'workday_job_family_job_family_group_data': {'rows': [row(job_family_id='f', job_family_group_id='g')]},
        'workday_job_family_group_data': {'rows': [row(id='g', job_family_group_code='G', summary=None)]},
        'workday_organization_job_family_data': {'rows': [row(job_family_id='f', job_family_group_id='wrong')]},
    }


def output(rows):
    return {'columns': [{'name': k, 'type': 'VARCHAR'} for k in rows[0]], 'rows': copy.deepcopy(rows)}


class WorkdayAuditTests(unittest.TestCase):
    def test_reverse_relations_match_hand_computed_members_without_organization_route(self):
        rows = audit.expected_rows(fixture())
        self.assertEqual(rows[0]['job_family_codes'], ['F'])
        self.assertEqual(rows[0]['job_family_summaries'], ['Ops, North'])
        self.assertEqual(rows[0]['job_family_group_codes'], ['G'])
        self.assertEqual(rows[0]['job_family_group_summaries'], [])
        self.assertEqual(rows[0]['job_profile_id'], '9007199254740993')

    def test_each_deleted_relation_dimension_and_profile_changes_expected_members(self):
        for table in ['job_family_job_profile', 'job_family', 'job_family_job_family_group', 'job_family_group']:
            data = fixture()
            for r in data['workday_' + table + '_data']['rows']:
                r['_fivetran_deleted'] = True
            rows = audit.expected_rows(data)
            self.assertEqual(rows[0]['job_family_group_codes'], [])
            if table in ['job_family_job_profile', 'job_family']:
                self.assertEqual(rows[0]['job_family_codes'], [])
            else:
                self.assertEqual(rows[0]['job_family_codes'], ['F'])
        data = fixture(); data['workday_job_profile_data']['rows'][0]['_fivetran_deleted'] = True
        self.assertEqual(audit.expected_rows(data), [])

    def test_empty_string_ambiguity_cannot_become_pass_or_wrong(self):
        self.assertEqual(audit.aggregate_verdict('', []), 'unknown')
        self.assertEqual(audit.aggregate_verdict(None, []), 'pass')
        self.assertEqual(audit.aggregate_verdict([], []), 'pass')
        self.assertEqual(audit.aggregate_verdict('', ['']), 'pass')
        self.assertEqual(audit.aggregate_verdict(None, ['']), 'fail')
        self.assertEqual(audit.aggregate_verdict('', ['F']), 'fail')

    def test_string_and_array_matching_keeps_member_boundaries_and_multiplicity(self):
        members = ['Ops, North', 'Beta', '']
        self.assertEqual(audit.aggregate_verdict('Beta, , Ops, North', members), 'pass')
        self.assertEqual(audit.aggregate_verdict(['', 'Beta', 'Ops, North'], members), 'pass')
        for bad in ['Ops, Beta, North, ', 'Beta, Beta, Ops, North', ['Beta', 'Beta', '']]:
            self.assertEqual(audit.aggregate_verdict(bad, members), 'fail')

    def test_keys_associations_duplicates_missing_rows_and_core_null_are_exact(self):
        rows = audit.expected_rows(fixture())
        second = copy.deepcopy(rows[0]);second.update(job_profile_id='9007199254740992', job_title='Other')
        rows.append(second)
        good = output(rows[::-1]); self.assertEqual(audit.compare_rows(rows, good)['status'], 'pass')
        swapped = copy.deepcopy(good)
        swapped['rows'][0]['job_title'], swapped['rows'][1]['job_title'] = 'Job', 'Other'
        self.assertEqual(audit.compare_rows(rows, swapped)['status'], 'fail')
        duplicate = output([rows[0], rows[0]])
        kinds = {d['kind'] for d in audit.compare_rows(rows, duplicate)['issues']}
        self.assertEqual(kinds, {'duplicate_key', 'missing_row'})
        for col, value in [('job_profile_id', 9007199254740993), ('private_title', '')]:
            bad = output(rows);bad['rows'][0][col] = value
            self.assertEqual(audit.compare_rows(rows, bad)['status'], 'fail')

    def test_missing_columns_fail_extra_column_and_column_order_are_allowed(self):
        rows = audit.expected_rows(fixture());good = output(rows)
        good['columns'].reverse();good['columns'].append({'name': 'extra', 'type': 'VARCHAR'})
        good['rows'][0]['extra'] = None
        self.assertEqual(audit.compare_rows(rows, good)['status'], 'pass')
        good['columns'] = [c for c in good['columns'] if c['name'] != 'private_title']
        self.assertEqual(audit.compare_rows(rows, good)['status'], 'fail')

    def test_unknown_workflow_is_not_success_and_data_failure_is_still_decisive(self):
        w = audit.workflow_verdict(None, None, None, ['model.workday.target'])
        self.assertEqual(w['status'], 'unknown')
        good = {'status': 'pass', 'issues': [], 'unknown': []}
        self.assertIsNone(audit.combine(good, w)['audit_reward'])
        bad = {'status': 'fail', 'issues': [{'kind': 'value'}], 'unknown': []}
        self.assertEqual(audit.combine(bad, w)['audit_reward'], 0)

    def test_complete_model_coverage_allows_select_but_test_or_partial_does_not(self):
        ids = ['model.workday.a', 'model.workday.target']
        obs = {'commands': [{'argv': ['dbt', c], 'returncode': 0} for c in ['deps', 'run']],
               'packages_present': True, 'target_model_present': True, 'relation_type': 'BASE TABLE'}
        m = {'metadata': {'invocation_id': 'a'}, 'nodes': {k: {'resource_type': 'model', 'config': {'enabled': True}} for k in ids}}
        rr = {'metadata': {'invocation_id': 'a'}, 'args': {'which': 'run', 'select': ['*']},
              'results': [{'unique_id': k, 'status': 'success'} for k in ids]}
        self.assertEqual(audit.workflow_verdict(obs, rr, m, ids)['status'], 'pass')
        rr['results'].pop()
        self.assertIn('all_models_not_executed', audit.workflow_verdict(obs, rr, m, ids)['issues'])
        rr['args']['which'] = 'test'
        self.assertIn('not_a_dbt_run', audit.workflow_verdict(obs, rr, m, ids)['issues'])

    def test_input_or_output_hash_tampering_and_nonfinite_json_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);p = root/'x.json';p.write_text('{}')
            ref = {'path':'x.json','sha256':audit.sha(p)}
            self.assertEqual(audit.verified(root, ref), p)
            p.write_text('{"x":NaN}')
            with self.assertRaises(ValueError):audit.verified(root, ref)
            with self.assertRaises(ValueError):audit.read(p)
            with self.assertRaises(ValueError):audit.verified(root, {'path':'../missing','sha256':'0'*64})

    def test_post_native_export_edit_cannot_be_rebound_as_original_submission(self):
        sys.path.insert(0, str(PATH.parents[4] / 'src'))
        from dbt_bench.io import evidence_ref, file_hash, tree_hash, write_json
        capture_spec = importlib.util.spec_from_file_location('workday_audit_capture', PATH.with_name('capture.py'))
        capture = importlib.util.module_from_spec(capture_spec);capture_spec.loader.exec_module(capture)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);case = root/'case';agent = root/'trial/agent';task = root/'task'
            for p in [case, agent, task]:p.mkdir(parents=True)
            (case/'engine.py').write_text('pass\n');(task/'instruction.md').write_text('fixture')
            write_json(case/'case.json', {'helpers':{'engine.py':file_hash(case/'engine.py')}})
            write_json(agent/'inputs.json', {'source':'fixed'});write_json(agent/'output.json', {'value':'original'})
            write_json(root/'trial/result.json', {'config':{'task':{'path':str(task)}}})
            write_json(agent/'case-proof.json', {'mode':'fixture','data_scope':'original_pinned_inputs',
                'input_artifacts':[{'path':'/logs/agent/inputs.json','sha256':file_hash(agent/'inputs.json')}],
                'submission_artifacts':[{'path':'/logs/agent/output.json','sha256':file_hash(agent/'output.json')}]})
            trial = {'mode':'fixture','data_scope':'original_pinned_inputs',
                'case':evidence_ref(case/'case.json',root),'case_hash':tree_hash(case),
                'result':evidence_ref(root/'trial/result.json',root),'validation_copy_hash':tree_hash(task),
                'independent_proof':evidence_ref(agent/'case-proof.json',root)}
            capture.verify_capture(root, trial)
            write_json(agent/'output.json', {'value':'changed'})
            with self.assertRaisesRegex(ValueError, 'captured export changed'):
                capture.verify_capture(root, trial)


if __name__ == '__main__':
    unittest.main()
