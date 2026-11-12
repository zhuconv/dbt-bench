"""Conservative evidence from raw locked Qwen session calls and results.

Only paired actual tool events count. Mentioning commands in messages, writing a
script containing commands, or commands run by the later audit do not count.
Unrecognized shell syntax/output is unknown; this is not a shell interpreter.
"""
import json
import re
import shlex
import posixpath
from pathlib import Path
from dbt_bench import io


def pairs(events):
    calls, result, gaps = {}, [], []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            gaps.append('Non-object session event'); continue
        for part in (event.get('message') or {}).get('parts', []):
            if not isinstance(part, dict):
                gaps.append('Non-object message part'); continue
            call = part.get('functionCall')
            if call and event.get('type') == 'assistant':
                ident = call.get('id')
                if not ident or ident in calls:
                    gaps.append('Missing/duplicate tool call ID'); continue
                calls[ident] = {'call_id': ident, 'name': call.get('name'),
                               'args': call.get('args') or {}, 'call_index': index,
                               'started_at': event.get('timestamp')}
            response = part.get('functionResponse')
            if response and event.get('type') == 'tool_result':
                ident = response.get('id')
                if ident not in calls or any(x['call_id'] == ident for x in result):
                    gaps.append('Orphan/duplicate tool response'); continue
                result.append(calls[ident] | {'response': response.get('response') or {},
                              'result_index': index, 'finished_at': event.get('timestamp')})
    if set(calls) != {x['call_id'] for x in result}:
        gaps.append('Unanswered tool call')
    return result, gaps


def shell_result(response):
    if response.get('error'):
        return None
    out = response.get('output')
    if isinstance(out, dict) and type(out.get('exitCode')) is int and isinstance(out.get('stdout'), str):
        return out['exitCode'], out['stdout']
    # Qwen shell text schema exposes labelled exit code and stdout. Do not infer
    # success from arbitrary output or a literal 'success' in model content.
    if isinstance(out, str):
        m = re.search(r'(?:^|\n)Exit Code: (-?\d+)(?:\n|$)', out)
        s = re.search(r'(?:^|\n)Stdout: ?(.*?)(?=\nStderr:|\nError:|\nExit Code:|\Z)', out, re.S)
        if m and s:
            return int(m.group(1)), s.group(1).strip('\n')
    return None


def simple_argv(command):
    if not isinstance(command, str) or any(t in command for t in (';', '|', '&', '\n', '`', '$(', '>', '<')):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def profile_build(command, cwd):
    """Recognize explicit local env binding on a successful simple dbt command.

    Only && chains of cd/export/env-assignment/dbt are accepted. No substitution,
    redirection, arbitrary executable, fallback, backgrounding or eval runs here.
    A final zero exit code proves all stages in this restricted chain executed.
    """
    if not isinstance(command, str) or any(x in command for x in ('\n', '`', '$(', ';', '|', '>', '<')):
        return None
    try:
        stages = [shlex.split(s.strip()) for s in command.split('&&')]
    except ValueError:
        return None
    env = {}; location = cwd if isinstance(cwd, str) and cwd.startswith('/') else None
    builds = []
    for argv in stages:
        if not argv or '&' in ' '.join(argv):
            return None
        if argv[0] == 'cd' and len(argv) == 2:
            location = argv[1] if argv[1].startswith('/') else posixpath.normpath(posixpath.join(location, argv[1])) if location else None
            continue
        if argv[0] == 'export':
            if len(argv) != 2 or not argv[1].startswith('DBT_PROFILES_DIR='):
                return None
            env['DBT_PROFILES_DIR'] = argv[1].split('=', 1)[1]
            continue
        local = dict(env)
        while argv and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=.*', argv[0]):
            name, value = argv.pop(0).split('=', 1); local[name] = value
        if not argv or Path(argv[0]).name != 'dbt' or not any(x in ('run', 'build') for x in argv[1:]):
            return None
        value = local.get('DBT_PROFILES_DIR')
        if not value or '$' in value or '~' in value:
            return None
        resolved = value if value.startswith('/') else posixpath.normpath(posixpath.join(location, value)) if location else None
        if resolved:
            builds.append(resolved)
    return builds or None


def inspect_events(events):
    paired, gaps = pairs(events)
    echoes, deps, writes, profiles = [], [], [], []
    for call in paired:
        name, args = call['name'], call['args']
        if name in ('write_file', 'replace', 'edit_file', 'apply_patch'):
            writes.append(call)
        if name != 'run_shell_command':
            continue
        cmd = args.get('command')
        argv = simple_argv(cmd)
        response = shell_result(call['response'])
        if response and response[0] == 0:
            located = profile_build(cmd, args.get('directory', args.get('cwd')))
            if located:
                profiles.append({'call_id': call['call_id'], 'profile_directories': located,
                                 'call_index': call['call_index'], 'result_index': call['result_index']})
        # Complex shell is not silently certified, including a combined echo
        # and heredoc. Preserve its call for later explicit review.
        if argv is None:
            gaps.append('Unrecognized compound shell action: ' + call['call_id'])
        if argv in (['echo', '$DB_TYPE'], ['printf', '%s\n', '$DB_TYPE']) and response == (0, 'duckdb'):
            echoes.append(call)
        if argv and Path(argv[0]).name == 'dbt' and 'deps' in argv[1:] and response and response[0] == 0:
            deps.append(call)
    before = None
    if echoes:
        first = min(echoes, key=lambda x: x['result_index'])
        earlier = [c for c in paired if c['call_id'] != first['call_id']
                   and c['call_index'] < first['result_index']]
        if any(c in writes for c in earlier):
            before = False
        else:
            readonly_tools = {'read_file', 'read_many_files', 'list_directory', 'glob', 'grep_search'}
            safe = all(c['name'] in readonly_tools or
                       (c['name'] == 'run_shell_command' and simple_argv(c['args'].get('command')) in
                        (['pwd'], ['echo', '$DB_TYPE'])) for c in earlier)
            identity_gaps = [g for g in gaps if not g.startswith('Unrecognized compound shell action:')]
            if safe and not identity_gaps:
                before = True
    return {'pairs': paired, 'gaps': gaps, 'echo_calls': echoes, 'deps_calls': deps,
            'write_calls': writes, 'profile_build_calls': profiles, 'echo_before_first_write': before,
            'scope': 'Actual paired raw Qwen events only; unsupported compound commands remain unresolved.'}


def inspect_trial(trial, root):
    paths = sorted((Path(trial) / 'agent/qwen-sessions').glob('**/chats/*.jsonl'))
    if len(paths) != 1:
        return {'pairs': [], 'gaps': ['Exactly one complete main Qwen session has not been established'],
                'echo_calls': [], 'deps_calls': [], 'write_calls': [],
                'profile_build_calls': [], 'echo_before_first_write': None, 'source_logs': []}
    events = []
    for number, line in enumerate(paths[0].read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            raise ValueError('Malformed original Qwen JSONL at line ' + str(number))
    return inspect_events(events) | {'source_logs': [io.evidence_ref(paths[0], root)]}
