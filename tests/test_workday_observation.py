import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'validation/spider2/workday002/workflow_observation_v1/observe.py'
spec = importlib.util.spec_from_file_location('workday_observer', PATH)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def raw_calls():
    calls = [{'id': key, 'name': 'run_shell_command', 'args': {'command': key}} for key in ['a', 'b']]
    events = [{'timestamp': 'now', 'message': {'parts': [{'functionCall': c} for c in calls]}}]
    for call in calls[::-1]:
        events.append({'timestamp': 'later', 'message': {'parts': [{'functionResponse': {
            'id': call['id'], 'name': call['name'], 'response': {'output': call['id']}}}]},
            'systemPayload': {'uiEvent': {'event.name': 'qwen-code.tool_call',
                'call_id': call['id'], 'function_name': call['name'], 'function_args': call['args']}}})
    return events


def log_block(iid, command, success, models):
    created = ''.join(f'1 of 1 OK created sql table model main.{m.rsplit(".",1)[1]} .... [OK in 0.1s]\n' for m in models) if success else ''
    return ('============================== 12:00:00.123456 | ' + iid + ' ==============================\n'
            "running dbt with arguments {'invocation_command': '" + command + "'}\n" + created +
            ''.join('Finished running node ' + m + '\n' for m in models) +
            'Command `dbt ' + command.split()[1] + '` ' + ('succeeded' if success else 'failed') + '\n')


def workflow_fixture():
    ids = ['model.workday.a', 'model.workday.b']
    nodes = {key: {'resource_type': 'model', 'config': {'enabled': True},
                   'original_file_path': 'models/' + key[-1] + '.sql', 'raw_code': 'select 1',
                   'schema': 'main', 'alias': key[-1]} for key in ids}
    manifest = {'nodes': nodes, 'child_map': {key: [] for key in ids}}
    full = {'finished_models': ids}
    calls = [
        dict(id='edit', line=2, name='write_file', args={'file_path': '/workspace/models/b.sql', 'content': 'select 1\n'}),
        dict(id='rerun', line=3, name='run_shell_command', args={'command': 'dbt run --select b'}),
        dict(id='test', line=4, name='run_shell_command', args={'command': 'dbt test'})]
    for c in calls:
        c.update(args_sha256=observer.digest(c['args']), response={'response': {'output': 'complete'}},
                 telemetry={'truncated': False, 'success': True})
    effects = {c['id']: {'args_sha256': c['args_sha256'], 'kind': k} for c, k in zip(calls, ['source_write', 'dbt_run', 'dbt_test'])}
    effects['edit']['node_id'] = ids[1]
    bound = [{'call_id': 'rerun', 'command': 'dbt run --select b', 'finished_models': [ids[1]]},
             {'call_id': 'test', 'command': 'dbt test', 'finished_models': []}]
    return ids, full, calls, effects, bound, manifest


class WorkdayObservationTests(unittest.TestCase):
    def test_parallel_calls_pair_by_id_and_unknown_auxiliary_events_fail(self):
        events = raw_calls()
        result = observer.qwen_calls(events)
        self.assertEqual([c['response']['response']['output'] for c in result['calls']], ['a', 'b'])
        events.append({'systemPayload': {'uiEvent': {'event.name': 'qwen-code.tool_call', 'call_id': 'nested'}}})
        with self.assertRaisesRegex(ValueError, 'Orphan'):
            observer.qwen_calls(events)
        result = observer.qwen_calls(events, ['nested'])
        self.assertEqual(result['auxiliary_telemetry'], {'nested': events[-1]['systemPayload']['uiEvent']})

    def test_duplicate_call_or_mismatched_tool_name_is_not_silently_merged(self):
        events = raw_calls();events.append(events[0])
        with self.assertRaisesRegex(ValueError, 'Duplicate tool call'):
            observer.qwen_calls(events)
        events = raw_calls();events[1]['message']['parts'][0]['functionResponse']['name'] = 'other'
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            observer.qwen_calls(events)

    def test_failed_run_and_final_test_do_not_erase_successful_full_run(self):
        ids = ['model.workday.a', 'model.workday.b']
        text = ''.join(log_block(str(i) * 8 + '-1111-2222-3333-' + str(i) * 12, command, ok, nodes)
                       for i, command, ok, nodes in [(1, 'dbt run', False, ids), (2, 'dbt run', True, ids), (3, 'dbt test', True, [])])
        history = observer.invocations(text)
        self.assertEqual([r['succeeded'] for r in history], [False, True, True])
        self.assertEqual(history[1]['finished_models'], ids)
        self.assertEqual(history[-1]['command'], 'dbt test')
        self.assertEqual(history[-1]['finished_models'], [])
        with self.assertRaisesRegex(ValueError, 'Duplicate dbt invocation'):
            observer.invocations(text + text)

    def test_tool_stdout_must_match_ids_relations_timestamp_and_be_untruncated(self):
        manifest = workflow_fixture()[-1]
        text = log_block('11111111-1111-2222-3333-111111111111', 'dbt run', True, ['model.workday.a'])
        invocation = observer.invocations(text)[0]
        command = 'cd /workspace && dbt run 2>&1'
        output = 'Command: ' + command + '\n12:00:00  Running with dbt=1.10.22\n1 of 1 OK created sql table model main.a .... [OK in 0.1s]\nError: (none)\nExit Code: 0\nSignal: (none)\n'
        call = dict(id='x', line=1, name='run_shell_command', args={'command': command}, args_sha256='hash',
                    response={'line': 2, 'response': {'output': output}}, telemetry={'truncated': False, 'success': True})
        self.assertEqual(observer.bind_invocation(call, invocation, manifest)['finished_models'], ['model.workday.a'])
        for bad in [output.replace('main.a', 'main.b'), output.replace('12:00:00', '12:00:01'), output.replace('Exit Code: 0', 'Exit Code: 1')]:
            changed = copy.deepcopy(call);changed['response']['response']['output'] = bad
            with self.assertRaises(ValueError):observer.bind_invocation(changed, invocation, manifest)
        call['telemetry']['truncated'] = True
        with self.assertRaisesRegex(ValueError, 'truncated'):observer.bind_invocation(call, invocation, manifest)

    def test_full_build_leaf_edit_rebuild_then_test_is_accepted(self):
        args = workflow_fixture()
        self.assertEqual(observer.assess_post_build(*args)['status'], 'pass')

    def test_missing_effect_review_rebuild_or_write_result_stays_unknown(self):
        args = workflow_fixture();del args[3]['test']
        self.assertEqual(observer.assess_post_build(*args)['status'], 'unknown')
        args = workflow_fixture();args[2].pop(1);del args[3]['rerun']
        self.assertIn('not subsequently rebuilt', observer.assess_post_build(*args)['reason'])
        args = workflow_fixture();args[2][0]['telemetry']['success'] = False
        self.assertEqual(observer.assess_post_build(*args)['status'], 'unknown')

    def test_nonleaf_changes_and_partial_only_coverage_cannot_pass(self):
        args = workflow_fixture();args[5]['child_map']['model.workday.b'] = ['model.workday.a']
        self.assertEqual(observer.assess_post_build(*args)['status'], 'unknown')
        args = workflow_fixture();args[1]['finished_models'] = ['model.workday.b']
        self.assertEqual(observer.assess_post_build(*args)['status'], 'unknown')
        args = workflow_fixture();args[3]['edit']['args_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'arguments changed'):observer.assess_post_build(*args)


if __name__ == '__main__':
    unittest.main()
