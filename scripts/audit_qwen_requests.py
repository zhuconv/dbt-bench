#!/usr/bin/env python3
"""Reconcile retained Qwen 0.26.5 requests with a complete, unretried session.

This audits existing artifacts; it neither converts nor overwrites native ATIF.
Compression, unknown events, failed requests and retries require separate review.
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import digest, evidence_ref, file_hash, read_json, read_jsonl, write_json, write_jsonl

VERSION = 'qwen-request-reconciliation-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text_content(content):
    if isinstance(content, str):
        return content
    require(isinstance(content, list), 'Unsupported request content')
    require(all(isinstance(p, dict) and set(p) == {'type', 'text'} and p['type'] == 'text'
                and isinstance(p['text'], str) for p in content), 'Nontext/malformed content requires separate review')
    return '\n'.join(p['text'] for p in content)


def response_signature(message):
    require(message.get('role') == 'assistant', 'Response role mismatch')
    calls = []
    for call in message.get('tool_calls') or []:
        require(call.get('type') == 'function' and bool(call.get('id')), 'Malformed response call')
        function = call['function']
        calls.append({'id': call['id'], 'name': function['name'], 'args': json.loads(function['arguments'])})
    return {'text': message.get('content') or '', 'thought': message.get('reasoning_content') or '', 'calls': calls}


def event_signature(event):
    parts = event['message']['parts']
    require(all(set(p) <= {'text', 'thought', 'functionCall'} for p in parts), 'Unknown assistant part')
    return {'text': '\n'.join(p['text'] for p in parts if 'text' in p and not p.get('thought')),
            'thought': '\n'.join(p['text'] for p in parts if p.get('thought')),
            'calls': [p['functionCall'] for p in parts if 'functionCall' in p]}


def reconcile(events, requests):
    """Validate IDs, payloads and lifecycle; return indices, never guessed prompts."""
    require(bool(events) and bool(requests), 'Missing events or requests')
    session_id = events[0]['sessionId']
    previous, uuids = None, set()
    api, starts, ends, tool_events, memories = {}, {}, {}, {}, []
    main_assistants, raw_results, attribution = [], {}, []
    last_response = None
    for i, event in enumerate(events):
        require(event.get('version') == '0.26.5' and event.get('sessionId') == session_id, 'Session/version mismatch')
        require(event.get('uuid') and event['uuid'] not in uuids and event.get('parentUuid') == previous, 'Broken/duplicate event chain')
        previous = event['uuid']; uuids.add(previous)
        kind = event.get('type')
        if kind == 'assistant':
            require(last_response is not None, 'Assistant has no preceding response telemetry')
            main_assistants.append((i, last_response, event_signature(event)))
            last_response = None
        elif kind == 'tool_result':
            for part in event['message']['parts']:
                require(set(part) == {'functionResponse'}, 'Unknown tool-result part')
                response = part['functionResponse']; cid = response['id']; payload = response['response']
                require(cid not in raw_results and set(payload) in ({'output'}, {'error'}), 'Duplicate/unsupported raw tool result')
                raw_results[cid] = {'index': i, 'payload': next(iter(payload.values()))}
        elif kind == 'user':
            require(event['message']['role'] == 'user', 'User role mismatch')
        elif kind == 'system':
            subtype = event.get('subtype')
            if subtype == 'memory_catalog_snapshot':
                parts = event['message']['parts']
                require(len(parts) == 1 and parts[0].get('partMetadata') == {'qwen-code:memory-catalog-snapshot': True}, 'Unknown memory catalog shape')
                memories.append({'index': i, 'text': parts[0]['text']})
            elif subtype == 'attribution_snapshot':
                snap = event['systemPayload']['snapshot']
                require(snap.get('type') == 'attribution-snapshot' and snap.get('version') == 1, 'Unknown attribution snapshot')
                attribution.append(i)
            elif subtype == 'ui_telemetry':
                item = event['systemPayload']['uiEvent']; name = item['event.name']
                if name == 'request_lifecycle':
                    require(item.get('phase') in {'started', 'ended'}, 'Unknown lifecycle phase')
                    target = starts if item['phase'] == 'started' else ends
                    key = item['executionId']; require(key not in target, 'Duplicate lifecycle')
                    target[key] = {'index': i, 'item': item}
                elif name == 'qwen-code.api_response':
                    key = item['response_id']; require(key not in api, 'Duplicate API response ID')
                    api[key] = {'index': i, 'item': item}; last_response = key
                elif name == 'qwen-code.tool_call':
                    key = item['call_id']; require(key not in tool_events, 'Duplicate telemetry call ID')
                    require(type(item.get('truncated')) is bool or item.get('status') == 'error', 'Unknown tool processing state')
                    tool_events[key] = {'index': i, 'item': item}
                else:
                    raise ValueError('Unknown telemetry event: ' + name)
            else:
                raise ValueError('Unsupported system event (including compression/retry): ' + str(subtype))
        else:
            raise ValueError('Unknown session event: ' + str(kind))

    require(set(starts) == set(ends), 'Unterminated request lifecycle')
    request_index = {}
    for position, request in enumerate(requests):
        require(request.get('error') is None and isinstance(request.get('response'), dict), 'Failed/incomplete interaction')
        response = request['response']; rid = response['id']
        require(rid not in request_index, 'Duplicate retained response ID')
        require(len(response['choices']) == 1 and response['choices'][0].get('finish_reason') in {'stop', 'tool_calls'}, 'Unfinished/unsupported response')
        require(request['context'].get('sessionId') == session_id, 'Request session mismatch')
        body = request['request']; messages = body['messages']; tools = body.get('tools')
        require(isinstance(tools, list) and tools, 'Missing real tool schemas')
        require(any(m.get('role') == 'system' and text_content(m['content']) for m in messages), 'Missing actual system prompt')
        require(all(t.get('type') == 'function' and t.get('function', {}).get('parameters') for t in tools), 'Incomplete tool schema')
        require(rid in api, 'Retained response has no telemetry')
        telemetry = api[rid]['item']; execution_id = telemetry['execution_id']
        require(execution_id in starts and execution_id in ends, 'Response lifecycle missing')
        start, end = starts[execution_id], ends[execution_id]
        require(start['index'] < end['index'] <= api[rid]['index'] and end['item'].get('outcome') == 'success', 'Failed/out-of-order lifecycle')
        prompt_id = request['context']['promptId']
        require(start['item']['promptId'] == end['item']['promptId'] == telemetry['prompt_id'] == prompt_id, 'Prompt association mismatch')
        require(telemetry.get('status_code') == 200 and body['model'] == response['model'] == telemetry['model'], 'Response/model mismatch')
        signature = response_signature(response['choices'][0]['message'])
        require(signature['text'] == telemetry['response_text'], 'Telemetry response content mismatch')
        declared = {t['function']['name'] for t in tools}
        require(all(c['name'] in declared for c in signature['calls']), 'Called tool has no request schema')
        request_index[rid] = {'position': position, 'prompt_id': prompt_id, 'execution_id': execution_id,
                             'start_index': start['index'], 'signature': signature}
    require(set(request_index) == set(api) and len(starts) == len(api), 'Request/telemetry coverage differs')
    main, main_ids = [], set()
    for event_index, rid, signature in main_assistants:
        require(rid in request_index and rid not in main_ids, 'Main response missing/duplicated')
        row = request_index[rid]
        require(row['prompt_id'].startswith(session_id + '########'), 'Subagent response assigned to main event')
        require(signature == row['signature'], 'Assistant text/reasoning/call payload differs')
        require(api[rid]['index'] < event_index, 'Assistant precedes response')
        prior = [sig for index, _, sig in main_assistants if index < row['start_index']]
        actual_history = [response_signature(m) for m in requests[row['position']]['request']['messages'] if m.get('role') == 'assistant']
        require(actual_history == prior, 'Main assistant history differs from preceding responses; compression/transformation needs review')
        main_ids.add(rid)
        main.append({'response_event_index': event_index, 'request_position': row['position'], 'response_id': rid,
                     'execution_id': row['execution_id'], 'response_signature_hash': digest(signature)})
    subagents = []
    for rid, row in request_index.items():
        if rid in main_ids:
            continue
        require(re.fullmatch(re.escape(session_id) + r'#managed-auto-memory-extractor-[\w-]+#\d+', row['prompt_id']), 'Unmapped main/unknown subagent request')
        subagents.append({'request_position': row['position'], 'response_id': rid, 'prompt_id': row['prompt_id'],
                         'response_signature_hash': digest(row['signature'])})

    calls, observed_results = {}, {}
    for rid, row in request_index.items():
        for call in row['signature']['calls']:
            cid = call['id']; require(cid not in calls and cid in tool_events, 'Duplicate/missing tool execution')
            item = tool_events[cid]['item']
            require(item['response_id'] == rid and item['function_name'] == call['name'] and item['function_args'] == call['args'], 'Tool ID/payload association differs')
            require(tool_events[cid]['index'] > api[rid]['index'], 'Tool execution precedes its response')
            calls[cid] = {'response_id': rid, 'call': call, 'request_position': row['position']}
    require(set(calls) == set(tool_events), 'Tool telemetry coverage differs')
    for request in requests:
        request_start = request_index[request['response']['id']]['start_index']
        for message in request['request']['messages']:
            if message.get('role') != 'tool':
                continue
            cid = message['tool_call_id']; require(cid in calls, 'Orphan request tool result')
            require(tool_events[cid]['index'] < request_start, 'Tool result appears before execution')
            value = text_content(message['content'])
            require(cid not in observed_results or observed_results[cid] == value, 'Tool result changed between requests')
            observed_results[cid] = value
            if cid in raw_results:
                require(raw_results[cid]['index'] < request_start, 'Raw tool result appears after consuming request')
                require(value == raw_results[cid]['payload'], 'Raw tool output differs from actual model input')
    require(set(calls) == set(observed_results), 'Unanswered/unrecorded tool result')
    require(set(raw_results) == {c['id'] for rid in main_ids for c in request_index[rid]['signature']['calls']}, 'Raw main tool-result coverage differs')
    memory_mapping = []
    for memory in memories:
        targets = []
        for rid in main_ids:
            row = request_index[rid]
            if row['start_index'] > memory['index']:
                body = requests[row['position']]['request']
                require(any(memory['text'] in text_content(m.get('content', '')) for m in body['messages'] if m.get('role') == 'user'), 'Memory catalog omitted/changed in actual context')
                targets.append(row['position'])
        require(targets, 'Unmapped memory snapshot')
        memory_mapping.append({'event_index': memory['index'], 'text_hash': digest(memory['text']), 'request_positions': sorted(targets)})
    return {'session_id': session_id, 'main': main, 'subagents': subagents, 'memory_mapping': memory_mapping,
            'attribution_event_indices': attribution, 'tool_result_hashes': {cid:digest(value) for cid,value in observed_results.items()},
            'native_tool_processing': [{'call_id':cid, 'event_index':row['index'],
                'truncated':row['item'].get('truncated'), 'status':row['item'].get('status'),
                'scope':'Native tool processing precedes the retained response; exact processed text is checked against subsequent actual requests. Pre-truncation environmental content is not reconstructed.'}
                for cid,row in tool_events.items() if row['item'].get('truncated') is not False],
            'counts': {'raw_events':len(events), 'requests':len(requests), 'main_generations':len(main),
                       'subagent_generations':len(subagents), 'main_tools':len(raw_results),
                       'all_tools':len(calls), 'memory_snapshots':len(memories), 'request_lifecycles':len(starts)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--slurm-dir', type=Path, required=True)
    parser.add_argument('--capture-conformance', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    sessions = list(args.trial.glob('agent/qwen-sessions/**/chats/*.jsonl'))
    require(len(sessions) == 1, 'Expected one retained main session')
    events = read_jsonl(sessions[0])
    request_paths = sorted(args.trial.glob('agent/openai-requests/*.json'))
    requests = [read_json(p) for p in request_paths]
    result = reconcile(events, requests)
    capture = read_json(args.capture_conformance)
    require(capture.get('passed') is True and capture.get('qwen_version') == '0.26.5', 'Capture conformance missing/failed')
    require({r['case'] for r in capture['cases']} == {'stream','nonstream','retry','http_error'}, 'Incomplete conformance cases')
    require(all(r['passed'] and r['request_body_matches_wire'] for r in capture['cases']), 'Capture conformance mismatch')
    require(next(r for r in capture['cases'] if r['case']=='retry')['all_http_attempts_recorded'] is False, 'Retry limitation not recorded')
    install = read_json(args.trial / 'agent/locked-install.json')
    inventory_path = ROOT / '.cache/qwen-runtime/locked-v1/inventory.json'
    require(install['installed'] and file_hash(inventory_path) == install['inventory_sha256'], 'Production installation inventory mismatch')
    inventory = {r['path']:r for r in read_json(inventory_path)}
    for source in capture['sources']:
        source_path = Path(source['path'])
        require(file_hash(source_path) == source['sha256'], 'Conformance source changed')
        suffix = '/@qwen-code/qwen-code/chunks/' + source_path.name
        entries = [r for p,r in inventory.items() if p.endswith(suffix)]
        require(len(entries)==1 and entries[0]['sha256'] == source['sha256'], 'Conformance code differs from production bundle')
    status = read_json(args.slurm_dir / 'status.json')
    require(status.get('complete') and len(status.get('trials', [])) == 1, 'Need completed single-trial service lifecycle')
    require(status['trials'][0]['id'] == read_json(args.trial / 'result.json')['id'], 'Wrong model service lifecycle')
    protocol_path = args.slurm_dir / 'protocol-response.json'
    protocol = read_json(protocol_path)
    require(protocol['id'] not in {r['response']['id'] for r in requests}, 'Protocol request counted as model trial')
    server_path = args.slurm_dir / 'vllm.log'
    statuses = re.findall(r'"POST /v1/chat/completions HTTP/1\.1" (\d+) ', server_path.read_text())
    require(len(statuses) == len(requests)+1 and set(statuses) == {'200'}, 'HTTP request count/status mismatch; possible hidden retries or missing interactions')
    result.update(schema_version=1, auditor_version=VERSION, passed=True,
        http_attempts=len(statuses), protocol_requests=1,
        native_atif_context_gap='Native ATIF omits memory_catalog_snapshot; it is preserved in actual requests and raw session. Native content_fidelity remains incomplete.',
        scope='Exact retained main/subagent requests and outputs for this completed, non-retried text-only session. No reconstruction from concatenated history. No claim for compressed, retried or unknown event types.',
        evidence=[evidence_ref(p,ROOT) for p in [Path(__file__),sessions[0],args.capture_conformance,inventory_path,
            args.trial/'agent/locked-install.json',args.slurm_dir/'status.json',protocol_path,server_path]],
        request_artifacts=[evidence_ref(p,ROOT) for p in request_paths])
    summary_path = out/'summary.json'
    write_json(summary_path,result)
    normalized=[]
    for mapping in result['main']:
        request = requests[mapping['request_position']]
        normalized.append({'response_event_index':mapping['response_event_index'],
            'request_id':mapping['response_id'], 'capture_unmodified':True,
            'request':request['request'], 'response':request['response'],
            'original':evidence_ref(request_paths[mapping['request_position']],ROOT)})
    context_path = out/'main-requests.jsonl';write_jsonl(context_path,normalized)
    write_json(out/'context-binding.json',{'kind':'full_requests','artifact':evidence_ref(context_path,ROOT),
        'capture_validation':evidence_ref(summary_path,ROOT),
        'scope':'All main generation requests; all memory subagent generations and results indexed in capture_validation.'})
    print(json.dumps({'passed':True,'counts':result['counts'],'summary':str(summary_path)}))


if __name__ == '__main__':
    main()
