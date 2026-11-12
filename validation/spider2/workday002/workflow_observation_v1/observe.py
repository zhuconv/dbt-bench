"""Scoped read-only workflow observations from fixed Qwen/dbt log schemas.

This is evidence extraction, not a generic shell interpreter or semantic scorer.
Post-build tool effects must be reviewed explicitly and bound to exact arguments.
"""
import ast
from collections import Counter
import hashlib
import json
import re

ANSI = re.compile(r'\x1b\[[0-9;]*m')
HEADER = re.compile(r'(?m)^=+ ([0-9:.]+) \| ([0-9a-f-]{36}) =+$')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def qwen_calls(events, reviewed_auxiliary_ids=()):
    calls, responses, telemetry = {}, {}, {}
    for line, event in enumerate(events, 1):
        for part in event.get('message', {}).get('parts', []):
            call = part.get('functionCall')
            response = part.get('functionResponse')
            if call:
                if call['id'] in calls:
                    raise ValueError('Duplicate tool call ID')
                calls[call['id']] = {**call, 'line': line, 'timestamp': event['timestamp'],
                                     'args_sha256': digest(call['args'])}
            if response:
                if response['id'] in responses:
                    raise ValueError('Duplicate tool result ID')
                responses[response['id']] = {**response, 'line': line, 'timestamp': event['timestamp']}
        t = event.get('systemPayload', {}).get('uiEvent', {})
        if t.get('event.name') == 'qwen-code.tool_call':
            if t['call_id'] in telemetry:
                raise ValueError('Duplicate tool telemetry ID')
            telemetry[t['call_id']] = t
    auxiliary_ids = set(telemetry) - set(calls)
    if set(responses) - set(calls) or auxiliary_ids != set(reviewed_auxiliary_ids):
        raise ValueError('Orphan tool response/telemetry')
    for call_id, call in calls.items():
        r, t = responses.get(call_id), telemetry.get(call_id)
        if r and (r['name'] != call['name'] or r['line'] <= call['line']):
            raise ValueError('Tool result ID/name/order mismatch')
        if t and (t['function_name'] != call['name'] or t['function_args'] != call['args']):
            raise ValueError('Telemetry and actual call disagree')
        call.update(response=r, telemetry=t)
    return {'calls': list(calls.values()),
            'auxiliary_telemetry': {key: telemetry[key] for key in sorted(auxiliary_ids)}}


def invocations(text):
    text = ANSI.sub('', text)
    boundaries = list(HEADER.finditer(text))
    result, seen = [], set()
    for index, header in enumerate(boundaries):
        iid = header[2]
        if iid in seen:
            raise ValueError('Duplicate dbt invocation ID')
        seen.add(iid)
        body = text[header.end():boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)]
        arguments = re.findall(r'(?m)^.*running dbt with arguments (\{.*\})$', body)
        ends = re.findall(r'Command `dbt (\w+)` (succeeded|failed)', body)
        if len(arguments) != 1 or len(ends) != 1:
            raise ValueError('Incomplete or ambiguous dbt invocation')
        args = ast.literal_eval(arguments[0])
        command = args['invocation_command']
        if not command.startswith('dbt ' + ends[0][0]):
            raise ValueError('dbt invocation command/end differ')
        nodes = re.findall(r'Finished running node (model\.[\w.]+)', body)
        success = re.findall(r'(\d+) of (\d+) OK created sql (\w+) model (\S+) ', body)
        result.append(dict(invocation_id=iid, start_time=header[1], command=command,
            succeeded=ends[0][1] == 'succeeded', finished_models=nodes,
            successful_relations=[dict(position=int(a), total=int(b), materialization=c, relation=d)
                                  for a, b, c, d in success],
            line_start=text[:header.start()].count('\n') + 1,
            line_end=text[:header.end() + len(body)].count('\n') + 1,
            body_sha256=hashlib.sha256(body.encode()).hexdigest()))
    return result


def bind_invocation(call, invocation, manifest):
    """Bind a reviewed exact command, full tool result and dbt log node IDs."""
    expected = 'cd /workspace && ' + invocation['command'] + ' 2>&1'
    if call['name'] != 'run_shell_command' or call['args'].get('command') != expected:
        raise ValueError('Command does not match reviewed invocation')
    response, telemetry = call['response'], call['telemetry']
    if not response or not telemetry or telemetry.get('truncated') is not False:
        raise ValueError('Missing or truncated native tool evidence')
    output = ANSI.sub('', response['response'].get('output', ''))
    if response['response'].get('error') or not re.search(r'\nError: \(none\)\nExit Code: 0\nSignal: \(none\)\n', output):
        raise ValueError('Native tool result does not establish successful execution')
    if not invocation['succeeded'] or telemetry.get('success') is not True:
        raise ValueError('Invocation/tool telemetry did not succeed')
    if not output.startswith('Command: ' + expected + '\n'):
        raise ValueError('Tool wrapper command differs')
    # The tool stdout has second-resolution dbt timestamps. It must include
    # the same invocation start, not only a matching command from another run.
    if invocation['start_time'].split('.')[0] + '  Running with dbt=' not in output:
        raise ValueError('Tool result and dbt invocation time differ')
    relations = re.findall(r'(\d+) of (\d+) OK created sql (\w+) model (\S+) ', output)
    parsed = [dict(position=int(a), total=int(b), materialization=c, relation=d) for a, b, c, d in relations]
    if parsed != invocation['successful_relations']:
        raise ValueError('Tool and dbt log successful relations disagree')
    if invocation['command'].split()[1] == 'run':
        ids = invocation['finished_models']
        if len(ids) != len(set(ids)) or len(ids) != len(parsed):
            raise ValueError('Incomplete/duplicate successful model observation')
        expected_relations = Counter(manifest['nodes'][k]['schema'] + '.' + manifest['nodes'][k]['alias'] for k in ids)
        if expected_relations != Counter(r['relation'] for r in parsed):
            raise ValueError('Model IDs and actual created relations differ')
    return dict(call_id=call['id'], call_line=call['line'], result_line=response['line'],
        invocation_id=invocation['invocation_id'], command=invocation['command'],
        finished_models=invocation['finished_models'], successful_relations=parsed,
        args_sha256=call['args_sha256'], result_sha256=digest(response['response']))


def assess_post_build(required_ids, full_run, later_calls, reviewed_effects, bound_runs, manifest):
    """Allow explicit leaf edits followed by real rebuild; never infer SQL effects."""
    required = set(required_ids)
    if Counter(full_run['finished_models']) != Counter(required_ids):
        return {'status': 'unknown', 'reason': 'Full required model set was not observed'}
    enabled = {k for k, n in manifest['nodes'].items() if n['resource_type'] == 'model' and n['config']['enabled']}
    if enabled != required:
        return {'status': 'unknown', 'reason': 'Changed model identities require separate review'}
    by_id = {r['call_id']: r for r in bound_runs}
    edits, pending = [], set()
    if set(reviewed_effects) != {c['id'] for c in later_calls}:
        return {'status': 'unknown', 'reason': 'Post-build tool effects are not fully reviewed'}
    for call in sorted(later_calls, key=lambda c: c['line']):
        effect = reviewed_effects[call['id']]
        if effect['args_sha256'] != call['args_sha256']:
            raise ValueError('Reviewed tool arguments changed')
        if not call['response'] or not call['telemetry'] or call['telemetry'].get('truncated'):
            return {'status': 'unknown', 'reason': 'Missing post-build tool result'}
        if effect['kind'] in {'read_only_query', 'outside_project_cli_install'}:
            continue
        if effect['kind'] == 'source_write':
            if call['telemetry'].get('success') is not True or call['response']['response'].get('error'):
                return {'status': 'unknown', 'reason': 'Source write did not establish successful completion'}
            node_id = effect['node_id']
            node = manifest['nodes'][node_id]
            if any(child in enabled for child in manifest['child_map'][node_id]):
                return {'status': 'unknown', 'reason': 'Changed model has downstream models; review rebuild closure'}
            if call['name'] != 'write_file' or call['args']['file_path'] != '/workspace/' + node['original_file_path']:
                raise ValueError('Reviewed source write path differs')
            if call['args']['content'].strip() != node['raw_code'].strip():
                return {'status': 'unknown', 'reason': 'Source edit differs from final model'}
            pending.add(node_id)
            edits.append({'call_id': call['id'], 'node_id': node_id, 'leaf': True})
        elif effect['kind'] in {'dbt_run', 'dbt_test'}:
            bound = by_id.get(call['id'])
            if not bound or bound['command'].split()[1] != effect['kind'].removeprefix('dbt_'):
                raise ValueError('dbt invocation not independently bound')
            if effect['kind'] == 'dbt_run':
                pending.difference_update(bound['finished_models'])
        else:
            return {'status': 'unknown', 'reason': 'Unreviewed modifying tool effect'}
    if pending:
        return {'status': 'unknown', 'reason': 'Changed source not subsequently rebuilt', 'unrebuilt': sorted(pending)}
    return {'status': 'pass', 'required_models_observed': len(required), 'leaf_edits': edits,
            'scope': 'Explicit reviewed command effects and source/log bindings only; not data correctness or complete model-context capture.'}
