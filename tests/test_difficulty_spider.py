"""Regression checks for evidence that must never become a measured success."""
import json
from pathlib import Path
import sys

import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
try:
    from difficulty_spider_registry import ENTRIES, qualified_entry
    from difficulty_spider_workflow import original_actions, unrestricted_build
    from difficulty_spider_score import typed_bag
    from difficulty_spider_registry import required_output_evidence
except ModuleNotFoundError as exc:
    if exc.name != 'yaml':
        raise
    raise unittest.SkipTest('Locked runtime PyYAML is required for reconstruction imports') from exc


def tool(command, ident='a', directory='/workspace', code=0):
    return [
        {'type': 'assistant', 'message': {'parts': [{'functionCall': {
            'id': ident, 'name': 'run_shell_command',
            'args': {'command': command, 'directory': directory}}}]}},
        {'type': 'tool_result', 'message': {'parts': [{'functionResponse': {
            'id': ident, 'response': {'output': {'exitCode': code, 'stdout': 'Completed successfully'}}}}]}}]


class SpiderDifficultyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_only_exact_sixteen_versions_are_supported(self):
        assert len(ENTRIES) == 16
        with self.assertRaisesRegex(ValueError, 'exact frozen'):
            qualified_entry({}, 'spider2__apple_store001', 'original-unpatched')

    def test_original_tool_error_is_not_hidden_by_success_output(self):
        events = tool('dbt run')
        response = events[1]['message']['parts'][0]['functionResponse']['response']
        response['error'] = 'Shell failed before dbt started'
        assert original_actions(events)['reward'] is None

    def test_fixture_receipt_cannot_be_counted_as_model_measurement(self):
        tmp_path = Path(self.directory.name)
        from reconcile_difficulty_v3 import audit_for_trial
        path = tmp_path / 'receipt.json'
        path.write_text(json.dumps({'fixture_only': True}))
        with self.assertRaisesRegex(ValueError, 'fixture'):
            audit_for_trial(path, {}, 'spider2__lever001', ENTRIES['lever001'][0], {})

    def check_unproved_or_restricted_build_is_unknown(self, command):
        assert original_actions(tool(command))['reward'] is None

    def test_unproved_or_restricted_build_is_unknown_1(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run --select target_model')

    def test_unproved_or_restricted_build_is_unknown_2(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt build --exclude broken_model')

    def test_unproved_or_restricted_build_is_unknown_3(self):
        self.check_unproved_or_restricted_build_is_unknown('echo dbt run')

    def test_unproved_or_restricted_build_is_unknown_4(self):
        self.check_unproved_or_restricted_build_is_unknown('python solve.py')

    def test_unproved_or_restricted_build_is_unknown_5(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run || true')

    def test_unproved_or_restricted_build_is_unknown_6(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run; echo success')

    def test_unproved_or_restricted_build_is_unknown_7(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run --target another')

    def test_unproved_or_restricted_build_is_unknown_8(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run --project-dir /another')

    def test_unproved_or_restricted_build_is_unknown_9(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run > /tmp/out')

    def test_unproved_or_restricted_build_is_unknown_10(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run --vars \'{"skip":true}\'')

    def test_unproved_or_restricted_build_is_unknown_11(self):
        self.check_unproved_or_restricted_build_is_unknown('dbt run --select=tag:target')

    def check_paired_actual_unrestricted_build(self, command):
        assert original_actions(tool(command))['reward'] == 1

    def test_paired_actual_unrestricted_build_1(self):
        self.check_paired_actual_unrestricted_build('dbt run')

    def test_paired_actual_unrestricted_build_2(self):
        self.check_paired_actual_unrestricted_build('dbt build --profiles-dir /workspace')

    def test_paired_actual_unrestricted_build_3(self):
        self.check_paired_actual_unrestricted_build('cd /workspace && dbt run --project-dir /workspace')

    def test_build_failure_is_not_repaired_by_a_successful_later_audit(self):
        assert original_actions(tool('dbt run', code=1))['reward'] is None
        # The actual event stream contains no command from the reconstruction.
        assert original_actions([])['reward'] is None

    def test_wrong_project_and_missing_call_id_never_prove_workflow(self):
        assert unrestricted_build('dbt run', '/tmp/project') is None
        assert original_actions(tool('dbt run')[1:])['reward'] is None

    def test_source_change_after_run_invalidates_action_binding(self):
        events = tool('dbt run')
        events += [{'type': 'assistant', 'message': {'parts': [{'functionCall': {
            'id': 'b', 'name': 'write_file', 'args': {'file_path': '/workspace/models/f.sql', 'content': 'select 0'}}}]}},
            {'type': 'tool_result', 'message': {'parts': [{'functionResponse': {
                'id': 'b', 'response': {'output': 'success'}}}]}}]
        assert original_actions(events)['reward'] is None

    def test_read_after_run_preserves_action_binding(self):
        events = tool('dbt run')
        events += [{'type': 'assistant', 'message': {'parts': [{'functionCall': {
            'id': 'b', 'name': 'read_file', 'args': {'file_path': '/workspace/models/f.sql'}}}]}},
            {'type': 'tool_result', 'message': {'parts': [{'functionResponse': {
                'id': 'b', 'response': {'output': 'select 1'}}}]}}]
        assert original_actions(events)['reward'] == 1

    def test_input_bags_preserve_multiplicity_types_and_large_ids(self):
        tmp_path = Path(self.directory.name)
        a, b = tmp_path / 'a.jsonl', tmp_path / 'b.jsonl'
        a.write_text('[9007199254740992, "A"]\n[2, "B"]\n')
        b.write_text('[2, "B"]\n[9007199254740992, "A"]\n')
        assert typed_bag(a) == typed_bag(b)
        b.write_text('[2, "B"]\n[9007199254740993, "A"]\n')
        assert typed_bag(a) != typed_bag(b)
        b.write_text('[9007199254740992, "A"]\n[2, "B"]\n[2, "B"]\n')
        assert typed_bag(a) != typed_bag(b)
        b.write_text('[9007199254740992, "A"]\n[{"$type":"float","value":"2.0"}, "B"]\n')
        assert typed_bag(a) != typed_bag(b)

if __name__ == '__main__':
    unittest.main()
