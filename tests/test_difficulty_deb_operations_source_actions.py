"""A source deliverable can satisfy echo/export without a model-issued build."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
try:
    from difficulty_deb_events import inspect_events
    from difficulty_deb_phase4_operations_v3 import original_action_gate
except ImportError as exc:
    raise unittest.SkipTest('Locked runtime dependencies unavailable: ' + str(exc))


def call(ident, name, args):
    return {'type': 'assistant', 'message': {'parts': [
        {'functionCall': {'id': ident, 'name': name, 'args': args}}]}}


def result(ident, text='', code=0):
    return {'type': 'tool_result', 'message': {'parts': [
        {'functionResponse': {'id': ident, 'response': {
            'output': f'Stdout: {text.rstrip()}\nStderr: \nExit Code: {code}'}}}]}}


class SourceActionTests(unittest.TestCase):
    def events(self, *, export=True, export_code=0, late_echo=False):
        echo = [call('echo', 'run_shell_command', {'command': 'echo "$DB_TYPE"'}), result('echo', 'duckdb\n')]
        write = [call('write', 'write_file', {'file_path': '/app/dbt_project/models/result.sql', 'content': 'select 1'}), result('write')]
        events = write + echo if late_echo else echo + write
        if export:
            events += [call('profile', 'run_shell_command', {
                'command': 'export DBT_PROFILES_DIR=/app/dbt_project', 'directory': '/app/dbt_project'}),
                result('profile', code=export_code)]
        return inspect_events(events)

    def test_paired_successful_export_does_not_require_a_build(self):
        events = self.events()
        self.assertEqual(events['profile_build_calls'], [])
        self.assertEqual(original_action_gate('dbt-daily-order-summary', events, '/app/dbt_project')['reward'], 1)

    def test_failed_export_is_not_action_evidence(self):
        self.assertIsNone(original_action_gate('dbt-daily-order-summary', self.events(export_code=1), '/app/dbt_project')['reward'])

    def test_echo_after_write_remains_failure(self):
        self.assertEqual(original_action_gate('dbt-daily-order-summary', self.events(late_echo=True), '/app/dbt_project')['reward'], 0)

    def test_non_export_contract_does_not_gain_an_export_obligation(self):
        self.assertEqual(original_action_gate('dbt-customer-risk-scoring', self.events(export=False), '/app/dbt_project')['reward'], 1)


if __name__ == '__main__':
    unittest.main()
