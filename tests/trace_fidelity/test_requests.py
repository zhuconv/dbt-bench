from copy import deepcopy
import unittest

from scripts.audit_qwen_requests import reconcile


def fixture():
    events, requests = [], []
    sid = 'a0000000-0000-4000-8000-000000000000'
    memory = 'Current memory catalog: fixture exact snapshot'
    tools = [{'type':'function','function':{'name':'inspect','parameters':{'type':'object'}}}]

    def event(kind, **kw):
        index = len(events)
        value = {'uuid':str(index), 'parentUuid':str(index-1) if index else None,
                 'sessionId':sid, 'version':'0.26.5', 'type':kind, **kw}
        events.append(value)
        return value

    def ui(value):
        return event('system', subtype='ui_telemetry', systemPayload={'uiEvent':value})

    event('user', message={'role':'user','parts':[{'text':'fixture task'}]})
    event('system', subtype='memory_catalog_snapshot', message={'role':'user','parts':[
        {'text':memory,'partMetadata':{'qwen-code:memory-catalog-snapshot':True}}]})
    history = [{'role':'system','content':'fixture exact system'}, {'role':'user','content':'fixture task\n'+memory}]
    for n in range(3):
        rid, execution = f'response-{n}', f'execution-{n}'
        prompt = sid+'########0' if n<2 else sid+'#managed-auto-memory-extractor-fixture#0'
        lifecycle = {'event.name':'request_lifecycle','executionId':execution,'promptId':prompt}
        ui({**lifecycle,'phase':'started'})
        ui({**lifecycle,'phase':'ended','outcome':'success'})
        ui({'event.name':'qwen-code.api_response','response_id':rid,'execution_id':execution,
            'prompt_id':prompt,'status_code':200,'model':'fixture','response_text':'answer'})
        message = {'role':'assistant','content':'answer','reasoning_content':'thinking'}
        parts = [{'text':'thinking','thought':True},{'text':'answer'}]
        if n==0:
            call = {'id':'call-a','name':'inspect','args':{'value':'A'}}
            message['tool_calls']=[{'id':call['id'],'type':'function','function':{'name':call['name'],'arguments':'{"value":"A"}'}}]
            parts.append({'functionCall':call})
        request = {'context':{'sessionId':sid,'promptId':prompt}, 'error':None,
            'request':{'model':'fixture','messages':deepcopy(history),'tools':deepcopy(tools)},
            'response':{'id':rid,'model':'fixture','choices':[{'message':deepcopy(message),'finish_reason':'stop'}]}}
        requests.append(request)
        if n<2:
            event('assistant',message={'role':'model','parts':parts})
        history.append(deepcopy(message))
        if n==0:
            ui({'event.name':'qwen-code.tool_call','call_id':'call-a','response_id':rid,
                'function_name':'inspect','function_args':{'value':'A'},'status':'success','truncated':False})
            event('tool_result',message={'role':'user','parts':[
                {'functionResponse':{'id':'call-a','response':{'output':'fixture output'}}}]})
            history.append({'role':'tool','tool_call_id':'call-a','content':'fixture output'})
    return events, requests


class RequestCaptureTests(unittest.TestCase):
    def test_main_subagent_and_actual_memory_are_mapped_separately(self):
        events, requests = fixture()
        result = reconcile(events, requests)
        self.assertEqual(result['counts']['main_generations'],2)
        self.assertEqual(result['counts']['subagent_generations'],1)
        self.assertEqual(result['memory_mapping'][0]['request_positions'],[0,1])
        self.assertEqual(result['main'][0]['response_id'],'response-0')

    def test_missing_or_duplicate_requests_cannot_pass(self):
        events, requests = fixture()
        for bad in [requests[:-1], requests+[deepcopy(requests[0])]]:
            with self.assertRaises(ValueError): reconcile(events,bad)

    def test_response_call_id_and_argument_changes_are_detected(self):
        for change in ['id','arguments','reasoning_content']:
            events, requests = fixture()
            message = requests[0]['response']['choices'][0]['message']
            if change=='id':message['tool_calls'][0]['id']='wrong-call'
            elif change=='arguments':message['tool_calls'][0]['function']['arguments']='{"value":"B"}'
            else:message['reasoning_content']='changed thought'
            with self.assertRaises(ValueError):reconcile(events,requests)

    def test_missing_system_tools_or_memory_is_not_filled_in(self):
        for change in ['system','tools','memory']:
            events, requests=fixture();body=requests[0]['request']
            if change=='system':body['messages'].pop(0)
            elif change=='tools':body['tools']=[]
            else:body['messages'][1]['content']='fixture task'
            with self.assertRaises(ValueError):reconcile(events,requests)

    def test_tool_output_loss_or_future_output_is_detected(self):
        events,requests=fixture()
        requests[1]['request']['messages'][-1]['content']=''
        with self.assertRaises(ValueError):reconcile(events,requests)
        events,requests=fixture()
        requests[0]['request']['messages'].append(deepcopy(requests[1]['request']['messages'][-1]))
        with self.assertRaisesRegex(ValueError,'before execution'):reconcile(events,requests)

    def test_empty_and_error_only_results_are_real_context(self):
        for payload in [{'output':''},{'error':'fixture ENOENT'}]:
            events,requests=fixture()
            event=next(e for e in events if e['type']=='tool_result')
            event['message']['parts'][0]['functionResponse']['response']=payload
            for request in requests:
                for message in request['request']['messages']:
                    if message['role']=='tool':message['content']=next(iter(payload.values()))
            self.assertEqual(reconcile(events,requests)['counts']['main_tools'],1)

    def test_truncation_is_declared_and_processed_text_must_still_match(self):
        events,requests=fixture()
        tool=next(e['systemPayload']['uiEvent'] for e in events
                  if e.get('systemPayload',{}).get('uiEvent',{}).get('event.name')=='qwen-code.tool_call')
        tool['truncated']=True
        result=reconcile(events,requests)
        self.assertTrue(result['native_tool_processing'][0]['truncated'])
        requests[1]['request']['messages'][-1]['content']='different truncation'
        with self.assertRaises(ValueError):reconcile(events,requests)

    def test_changed_history_and_unknown_compression_stay_unreviewed(self):
        events,requests=fixture()
        requests[1]['request']['messages'][2]['content']='compressed history'
        with self.assertRaisesRegex(ValueError,'history differs'):reconcile(events,requests)
        events,requests=fixture()
        events.append({'uuid':'new','parentUuid':events[-1]['uuid'],'sessionId':events[0]['sessionId'],
            'version':'0.26.5','type':'system','subtype':'chat_compression'})
        with self.assertRaisesRegex(ValueError,'compression/retry'):reconcile(events,requests)

    def test_failed_or_unterminated_lifecycle_is_not_complete(self):
        events,requests=fixture()
        ending=next(e['systemPayload']['uiEvent'] for e in events
                    if e.get('systemPayload',{}).get('uiEvent',{}).get('phase')=='ended')
        ending['outcome']='error'
        with self.assertRaisesRegex(ValueError,'lifecycle'):reconcile(events,requests)

    def test_terminal_memory_success_metadata_does_not_replace_result_body(self):
        # The Workday trace ends with a successful memory write, after the last
        # generation. Its telemetry includes a length but no returned text.
        events, requests = fixture()
        last = requests[-1]
        args = {'file_path': '/memory/MEMORY.md', 'content': 'fixture memory'}
        last['request']['tools'].append({'type': 'function', 'function': {
            'name': 'write_file', 'parameters': {'type': 'object'}}})
        last['response']['choices'][0]['message']['tool_calls'] = [{
            'id': 'terminal-memory-write', 'type': 'function',
            'function': {'name': 'write_file',
                         'arguments': '{"file_path":"/memory/MEMORY.md","content":"fixture memory"}'}}]
        events.append({'uuid': str(len(events)), 'parentUuid': events[-1]['uuid'],
            'sessionId': events[0]['sessionId'], 'version': '0.26.5', 'type': 'system',
            'subtype': 'ui_telemetry', 'systemPayload': {'uiEvent': {
                'event.name': 'qwen-code.tool_call', 'call_id': 'terminal-memory-write',
                'response_id': last['response']['id'], 'function_name': 'write_file',
                'function_args': args, 'status': 'success', 'success': True,
                'content_length': 78, 'raw_content_length': 78, 'truncated': False}}})
        with self.assertRaisesRegex(ValueError, 'Unanswered/unrecorded tool result'):
            reconcile(events, requests)
