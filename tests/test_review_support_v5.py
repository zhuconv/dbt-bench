from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from phase4_workflow_v5_replay_v2 import support, with_workflow_v5
import review_support_v4 as old


class WorkflowV5Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        files = {}
        for name, text in {
            'dbt_project.yml': 'profile: local\n',
            'profiles.yml': 'local:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: /data/input.duckdb\n      schema: analytics\n',
            'models/mart.sql': 'select 1 as id',
        }.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            files[name] = path

        def build(name, argv):
            return {'run': {'command': argv, 'returncode': 0},
                    'manifest': {'nodes': {name: {'name': name, 'resource_type': 'model',
                        'original_file_path': 'models/mart.sql', 'raw_code': 'select 1 as id',
                        'schema': 'analytics', 'config': {'materialized': 'table'}}}},
                    'run_results': {'results': [{'unique_id': name, 'status': 'success'}]}}

        deps = {'run': {'command': ['dbt', 'deps', '--project-dir', '/app/reference'], 'returncode': 0},
                'manifest': None, 'run_results': None}
        commands = [deps, build('source', ['dbt', 'run', '--project-dir', '/app/reference']),
                    build('mart', ['dbt', 'run'])]
        self.verified = {'request': {'project_root': '/app/project'},
            'execution': {'environment': {'DBT_PROFILES_DIR': '/app/project'},
                          'runs': [c['run'] for c in commands]},
            'final_files': files, 'commands': commands,
            'backend_shell': {'exit_code': 0, 'output': 'duckdb\n', 'command': 'echo "$DB_TYPE"'}}
        self.policy = {'project_root': '/app/project', 'database': '/data/input.duckdb',
                       'models': ['mart'], 'deps_required': True, 'upstream_models': ['source']}

    def test_real_deps_shape_crashes_v4_and_passes_v5(self):
        with self.assertRaises(TypeError):
            old.workflow(self.verified, self.policy)
        self.assertEqual(support.workflow(self.verified, self.policy)['reward'], 1)

    def test_omitted_deps_still_rejected_and_other_checks_equal(self):
        v = deepcopy(self.verified)
        v['commands'] = v['commands'][1:]
        v['execution']['runs'] = v['execution']['runs'][1:]
        new = support.workflow(v, self.policy)
        self.assertEqual(new, old.workflow(v, self.policy))
        self.assertEqual(new['checks']['dependencies']['status'], 'fail')
        self.assertEqual(new['checks']['upstream']['status'], 'pass')
        self.assertEqual(new['reward'], 0)

    def test_deps_is_not_evidence_of_upstream_build(self):
        v = deepcopy(self.verified)
        v['commands'].pop(1)
        v['execution']['runs'].pop(1)
        result = support.workflow(v, self.policy)
        self.assertEqual(result['checks']['dependencies']['status'], 'pass')
        self.assertEqual(result['checks']['upstream']['status'], 'fail')
        self.assertEqual(result['reward'], 0)

    def test_failed_upstream_model_not_counted(self):
        v = deepcopy(self.verified)
        v['commands'][1]['run_results']['results'][0]['status'] = 'error'
        self.assertEqual(support.workflow(v, self.policy)['checks']['upstream']['status'], 'fail')

    def test_compile_manifest_does_not_prove_materialization(self):
        v = deepcopy(self.verified)
        v['commands'][1]['run']['command'][1] = 'compile'
        self.assertEqual(support.workflow(v, self.policy)['checks']['upstream']['status'], 'fail')

    def test_actual_build_command_counts(self):
        v = deepcopy(self.verified)
        v['commands'][1]['run']['command'][1] = 'build'
        self.assertEqual(support.workflow(v, self.policy)['reward'], 1)

    def test_dependency_rebinding_does_not_recurse_or_mutate_old_globals(self):
        namespace = {'workflow': old.workflow}
        exec('def check_workflow(v, p):\n return workflow(v, p)\n', namespace)
        original = namespace['check_workflow']
        revised = with_workflow_v5(original)
        self.assertEqual(revised(self.verified, self.policy)['reward'], 1)
        self.assertIs(original.__globals__['workflow'], old.workflow)
        with self.assertRaises(TypeError):
            original(self.verified, self.policy)
