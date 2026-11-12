import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'validation/spider2/workday002/workflow_v2/workflow_proof.py'
spec = importlib.util.spec_from_file_location('workday_workflow_case_proof', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkdayWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ids = ['model.workday.a', 'model.workday.target']
        self.state = {'packages_present': True, 'target_model_present': True, 'relation_type': 'BASE TABLE',
                      'commands': [{'argv': ['dbt', c], 'returncode': 0} for c in ['deps', 'run']]}
        self.manifest = {'metadata': {'invocation_id': 'a'},
                         'nodes': {k: {'resource_type': 'model', 'config': {'enabled': True}} for k in self.ids}}
        self.run = {'metadata': {'invocation_id': 'a'}, 'args': {'which': 'run'},
                    'results': [{'unique_id': k, 'status': 'success'} for k in self.ids]}

    def check(self):
        return module.violations(self.state, self.run, self.manifest, self.ids)

    def test_explicit_full_selection_is_legal_but_partial_build_is_not(self):
        self.run['args']['select'] = ['*']
        self.state['commands'][-1]['argv'] += ['--select', '*']
        self.assertEqual(self.check(), [])
        self.run['results'].pop()
        self.assertIn('all_models_not_executed', self.check())

    def test_disabling_or_duplicating_a_model_cannot_satisfy_all_models(self):
        self.manifest['nodes'][self.ids[0]]['config']['enabled'] = False
        self.run['results'] = [self.run['results'][1]] * 2
        self.assertIn('enabled_model_set_changed', self.check())
        self.assertIn('all_models_not_executed', self.check())

    def test_test_artifacts_or_mixed_invocations_are_not_run_evidence(self):
        self.run['args']['which'] = 'test'
        self.assertIn('artifact_is_not_run', self.check())
        self.run['args']['which'] = 'run'
        self.manifest['metadata']['invocation_id'] = 'b'
        self.assertIn('invocation_mismatch', self.check())

    def test_correct_table_alone_does_not_meet_dbt_workflow(self):
        self.state['commands'] = []
        self.state['target_model_present'] = False
        self.assertEqual(module.violations(self.state, None, None, self.ids),
                         ['deps_not_run', 'target_model_missing', 'dbt_run_not_executed', 'run_artifacts_missing'])

    def test_actual_relation_and_final_source_matter(self):
        self.assertEqual(self.check(), [])
        self.state['relation_type'] = 'VIEW'
        self.assertEqual(self.check(), ['target_is_not_table'])
        self.state['relation_type'] = 'BASE TABLE'
        self.state['target_model_present'] = False
        self.assertEqual(self.check(), ['target_model_missing'])

    def test_deps_is_conditional_on_a_present_package_manifest(self):
        self.state['commands'] = self.state['commands'][1:]
        self.state['packages_present'] = False
        self.assertEqual(self.check(), [])
        self.state['packages_present'] = True
        self.assertEqual(self.check(), ['deps_not_run'])

    def test_failed_deps_or_model_execution_not_hidden_by_artifact_presence(self):
        self.state['commands'][0]['returncode'] = 1
        self.run['results'][0]['status'] = 'error'
        self.assertEqual(self.check(), ['deps_not_run', 'model_execution_failed'])


if __name__ == '__main__':
    unittest.main()
