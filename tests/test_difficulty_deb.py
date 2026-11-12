import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_deb_events import inspect_events, pairs, shell_result, profile_build
HAS_RUNTIME_YAML = importlib.util.find_spec('yaml') is not None
RUNTIME_REASON = 'Requires PyYAML from the locked Harbor runtime; base stdlib environment is intentionally insufficient.'
if HAS_RUNTIME_YAML:
    from difficulty_deb_binding_v2 import assert_source_inventory
    from difficulty_deb_score_v4 import typed_bag
    from difficulty_deb_workflow_v3 import declared_action_gate, spec_workflow
    from prepare_difficulty_audit import source_plan
    from reconcile_difficulty import audit_for_trial


def call(ident, name, args):
    return {'type': 'assistant', 'message': {'parts': [{'functionCall': {
        'id': ident, 'name': name, 'args': args}}]}}


def result(ident, output=None, error=None):
    response = {'error': error} if error is not None else {'output': output}
    return {'type': 'tool_result', 'message': {'parts': [{'functionResponse': {
        'id': ident, 'response': response}}]}}


class OriginalActions(unittest.TestCase):
    def test_actual_paired_echo_before_write(self):
        events = [call('e', 'run_shell_command', {'command': 'echo "$DB_TYPE"'}),
                  result('e', {'exitCode': 0, 'stdout': 'duckdb'}),
                  call('w', 'write_file', {'path': '/app/dbt_project/models/m.sql'}), result('w', 'ok')]
        self.assertIs(inspect_events(events)['echo_before_first_write'], True)

    def test_parallel_echo_completion_is_not_before_write(self):
        events = [call('e', 'run_shell_command', {'command': 'echo "$DB_TYPE"'}),
                  call('w', 'write_file', {'path': 'm.sql'}),
                  result('e', {'exitCode': 0, 'stdout': 'duckdb'}), result('w', 'ok')]
        self.assertIs(inspect_events(events)['echo_before_first_write'], False)

    def test_model_message_and_written_script_do_not_prove_deps(self):
        events = [{'type': 'assistant', 'message': {'parts': [{'text': 'dbt deps succeeded'}]}},
                  call('w', 'write_file', {'path': 'build.sh', 'content': 'dbt deps'}), result('w', 'ok')]
        self.assertEqual(inspect_events(events)['deps_calls'], [])

    def test_error_only_result_is_preserved_and_never_success(self):
        events = [call('d', 'run_shell_command', {'command': 'dbt deps'}), result('d', error='npm ENOENT')]
        parsed = inspect_events(events)
        self.assertEqual(parsed['deps_calls'], [])
        self.assertEqual(parsed['pairs'][0]['response']['error'], 'npm ENOENT')

    def test_unrecognized_compound_command_remains_unknown(self):
        parsed = inspect_events([call('d', 'run_shell_command', {'command': 'echo "$DB_TYPE"; cat > m.sql <<EOF\nselect 1\nEOF'}),
                                 result('d', {'exitCode': 0, 'stdout': 'duckdb'})])
        self.assertIsNone(parsed['echo_before_first_write'])
        self.assertTrue(parsed['gaps'])

    def test_duplicate_and_orphan_ids_do_not_silently_match(self):
        _, gaps = pairs([result('orphan', 'ok'), call('x', 'write_file', {}), call('x', 'write_file', {})])
        self.assertEqual(len(gaps), 3)

    def test_unlabelled_success_text_is_not_shell_exit_evidence(self):
        self.assertIsNone(shell_result({'output': 'duckdb success exit 0'}))

    @unittest.skipUnless(HAS_RUNTIME_YAML, RUNTIME_REASON)
    def test_actual_profile_bound_build_can_satisfy_action_gate(self):
        events = [call('e', 'run_shell_command', {'command': 'echo "$DB_TYPE"'}),
                  result('e', {'exitCode': 0, 'stdout': 'duckdb'}),
                  call('b', 'run_shell_command', {'command': 'cd /app/dbt_project && DBT_PROFILES_DIR=. dbt run'}),
                  result('b', {'exitCode': 0, 'stdout': 'Completed successfully'})]
        observed = inspect_events(events)
        self.assertEqual(declared_action_gate('deb__dbt-order-reconciliation', observed, '/app/dbt_project')['reward'], 1)

    def test_export_in_separate_shell_does_not_prove_later_build_environment(self):
        self.assertIsNone(profile_build('export DBT_PROFILES_DIR=/app/dbt_project', None))
        self.assertIsNone(profile_build('dbt run', '/app/dbt_project'))

    def test_shell_fallback_cannot_hide_failed_prescribed_build(self):
        self.assertIsNone(profile_build('DBT_PROFILES_DIR=/app/dbt_project dbt run || true', None))
        self.assertIsNone(profile_build('DBT_PROFILES_DIR=/app/dbt_project dbt run; true', None))


@unittest.skipUnless(HAS_RUNTIME_YAML, RUNTIME_REASON)
class SourceIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.source = self.root / 'models/m.sql'; self.source.parent.mkdir(); self.source.write_text('select 1')
        self.item = {'path': 'models/m.sql', 'sha256': io.file_hash(self.source),
                     'captured_source': io.evidence_ref(self.source, self.root)}
        self.original = {'models/m.sql': {'sha256': self.item['sha256']}}
        self.extracted = {'source_files': [self.item]}
        self.execution = {f: [{'relative_path': 'models/m.sql', 'exists': True, 'sha256': self.item['sha256']}]
                          for f in ('submitted_files', 'final_files')}

    def tearDown(self):
        self.tmp.cleanup()

    def test_actual_delivered_bytes_pass_without_author_whitelist(self):
        assert_source_inventory(self.extracted, self.original, ['models/m.sql'], self.execution, self.root)

    def test_modified_extracted_source_rejected(self):
        self.source.write_text('select * from hidden_gold')
        with self.assertRaisesRegex(ValueError, 'Substituted'):
            assert_source_inventory(self.extracted, self.original, ['models/m.sql'], self.execution, self.root)

    def test_rebuild_source_substitution_rejected(self):
        self.execution['final_files'][0]['sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'Rebuild changed'):
            assert_source_inventory(self.extracted, self.original, ['models/m.sql'], self.execution, self.root)

    def test_fake_target_is_discarded_even_if_capture_includes_it(self):
        (self.root / 'dbt_project.yml').write_text('name: user\ntarget-path: forged_target\n')
        target = self.root / 'forged_target/manifest.json'; target.parent.mkdir(); target.write_text('{"success":true}')
        files = [{'path': str(p.relative_to(self.root)), 'sha256': io.file_hash(p),
                  'captured_source': io.evidence_ref(p, self.root)} for p in (self.source, target, self.root / 'dbt_project.yml')]
        _, names, _ = source_plan({'source_dir': str(self.root), 'source_files': files},
                                  {'initial_trees': [], 'initial_files': []}, {'fresh_artifact_paths': []})
        self.assertNotIn('forged_target/manifest.json', names)
        self.assertIn('models/m.sql', names)

    def test_fixture_receipt_cannot_enter_real_four_attempt_count(self):
        p = self.root / 'receipt.json'
        io.write_json(p, {'fixture_only': True, 'training_prohibited': True})
        with self.assertRaisesRegex(ValueError, 'fixture'):
            audit_for_trial(p, {}, 'deb__x', 'version', {})

    def test_raw_input_comparison_preserves_big_integer_and_duplicates(self):
        a, b = self.root / 'a.jsonl', self.root / 'b.jsonl'
        a.write_text('[9007199254740992]\n[9007199254740992]\n')
        b.write_text('[9007199254740993]\n[9007199254740992]\n')
        self.assertNotEqual(typed_bag(a), typed_bag(b))
        b.write_text('[9007199254740992]\n')
        self.assertNotEqual(typed_bag(a), typed_bag(b))

    def test_raw_timestamp_serialization_is_not_a_value_change(self):
        a, b = self.root / 'a.jsonl', self.root / 'b.jsonl'
        a.write_text('[{"$type":"datetime","value":"2024-01-01 00:00:00"}]\n')
        b.write_text('[{"$type": "datetime", "value": "2024-01-01T00:00:00"}]\n')
        self.assertEqual(typed_bag(a), typed_bag(b))

    def test_real_funnel_inspector_structured_result_keeps_status(self):
        # The frozen funnel inspector returns {source_lineage: check}; other
        # task inspectors return check directly. Empty actual parsed input must
        # remain an explicit gap, not raise KeyError in the frozen workflow.
        manifest = self.root / 'manifest.json'; io.write_json(manifest, {'nodes': {}})
        probe = self.root / 'probe.json'; io.write_json(probe, {'parsed': []})
        output = self.root / 'workflow-output'; output.mkdir()
        context = {'binding': {'task_key': 'deb__dbt-sales-funnel-analysis'},
            'manifest': {'source_binding': io.evidence_ref(manifest, ROOT)}, 'source_names': [],
            'fresh': {'commands': [{'manifest': {'nodes': {}}, 'manifest_path': manifest,
                'run_results': {'results': []}, 'run': {'command': ['dbt','run'], 'cwd': '/app/dbt_project'}}],
                'final_files': {}, 'execution': {'environment': {}}, 'initial': {}}}
        reference = spec_workflow(context, {'probe': io.evidence_ref(probe, ROOT)},
                                  {'echo_before_first_write': None}, output, ROOT)
        facts = io.read_json(ROOT / reference['path'])
        self.assertEqual(facts['source_lineage']['status'], 'gap')
        self.assertIn('cannot establish', facts['source_lineage']['reason'])


if __name__ == '__main__':
    unittest.main()
