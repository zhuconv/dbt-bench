"""Spider source graph checks and conservative original Qwen action evidence."""
from pathlib import Path
import posixpath
import shlex
from difficulty_deb_events import shell_result, pairs


def unrestricted_build(command, directory):
    """Recognize a narrow, successful shell chain; never execute shell text."""
    if not isinstance(command, str) or any(x in command for x in ('\n', '`', '$', ';', '|', '>', '<', '||')):
        return None
    try:
        chain = [shlex.split(x) for x in command.split('&&')]
    except ValueError:
        return None
    cwd = directory if directory == '/workspace' else None
    found = []
    for argv in chain:
        if not argv or any('&' in x for x in argv):
            return None
        if argv[0] == 'cd' and len(argv) == 2:
            cwd = posixpath.normpath(argv[1]) if argv[1].startswith('/') else posixpath.normpath(posixpath.join(cwd, argv[1])) if cwd else None
            continue
        if len(argv) < 2 or Path(argv[0]).name != 'dbt' or argv[1] not in ('run', 'build'):
            return None
        # An explicit project/profile/target selector may change the graph or
        # environment. Support only the reviewed workspace's default target.
        allowed = {'--profiles-dir': '/workspace', '--project-dir': '/workspace'}
        rest = argv[2:]
        while rest:
            if len(rest) < 2 or rest[0] not in allowed or rest[1] != allowed[rest[0]]:
                return None
            rest = rest[2:]
        if cwd != '/workspace':
            return None
        found.append(argv)
    return found or None


def original_actions(events):
    paired, gaps = pairs(events)
    candidates = []
    for event in paired:
        if event['name'] != 'run_shell_command':
            continue
        args = event['args']
        result = shell_result(event['response'])
        built = unrestricted_build(args.get('command'), args.get('directory', args.get('cwd')))
        if built and result and result[0] == 0:
            candidates.append(event)
    if gaps or not candidates:
        return {'status': 'unknown', 'reward': None, 'reason': 'No complete paired successful all-model build in the actual workspace is established.', 'gaps': gaps, 'build_calls': candidates}
    last = max(candidates, key=lambda x: x['result_index'])
    later = [x for x in paired if x['call_index'] > last['result_index']]
    readonly = {'read_file', 'read_many_files', 'list_directory', 'glob', 'grep_search'}
    unresolved = [x['call_id'] for x in later if x['name'] not in readonly]
    if unresolved:
        return {'status': 'unknown', 'reward': None, 'reason': 'Later original tool actions may have changed the built submission; no unobserved replay assumption.', 'later_unresolved_calls': unresolved, 'build_calls': candidates}
    return {'status': 'scored', 'reward': 1, 'build_calls': candidates,
            'scope': 'Actual paired Qwen tool result: successful unrestricted dbt run/build in /workspace, with no later potentially mutating action. Rebuild commands are never used here.'}


def fresh_graph(fresh, raw, observation, baseline):
    """Authenticate graph/source delivery independently of the model log claim."""
    if not fresh['execution'].get('build_success'):
        return {'status': 'unknown', 'reward': None,
                'reason': 'Exact source reconstruction failed; original submitted table failures can still be scored independently. Failure cause needs review.',
                'runs': fresh['execution']['runs']}
    command = fresh['commands'][-1]
    graph, results = command['manifest'], command['run_results']
    nodes = {k: n for k, n in graph['nodes'].items()
             if n.get('resource_type') == 'model' and n.get('config', {}).get('enabled', True)}
    executable = {k for k, n in nodes.items() if n['config'].get('materialized') != 'ephemeral'}
    success = {x['unique_id'] for x in results['results'] if x['status'] == 'success'}
    columns = observation.get('outputs', [])
    required = {x['relation'].removeprefix('main.') for x in columns
                if x['database'] == raw['database'] and not x['relation'].startswith('main.')}
    if not required:
        raise ValueError('Qualified observation lacks explicit target relations')
    target = [n for n in nodes.values() if n.get('alias') in required
              and n.get('schema') == 'main' and n.get('database') == Path(raw['database']).stem]
    checks = {'all_enabled_models_built': executable <= success and all(x['status'] == 'success' for x in results['results']),
              'required_target_models': len(target) == len(required) and {n['alias'] for n in target} == required,
              'required_targets_built': all(n['unique_id'] in success for n in target)}
    source_ok = True
    for node in nodes.values():
        path = node['original_file_path']
        if node['package_name'] != graph['metadata']['project_name']:
            continue
        file = fresh['final_files'].get(path)
        source_ok &= file is not None and file.read_text().strip() == node.get('raw_code', '').strip()
    checks['executed_graph_matches_actual_delivered_sources'] = bool(source_ok)
    initial = {t['relative_path']: t for t in baseline['initial_trees']}
    final = {t['relative_path']: t for t in fresh['execution']['final_trees']}
    checks['frozen_packages_unchanged'] = ('dbt_packages' in initial and 'dbt_packages' in final
        and initial['dbt_packages']['files'] == final['dbt_packages']['files'])
    return {'status': 'scored', 'reward': int(all(checks.values())), 'checks': checks,
            'required_relations': sorted(required), 'invocation_id': graph['metadata']['invocation_id'],
            'scope': 'Exact submitted-source clean build only. Separate original_actions must prove the model actually ran all models.'}
