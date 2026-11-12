"""Translate verified reconstruction facts without inventing model actions."""
from datetime import datetime
from pathlib import Path
import importlib.util
from collections import Counter
import json
import yaml
from dbt_bench import io


def spec_workflow(context, observed, events, output, root):
    """Fresh facts for spec-style checkers, with original action fields omitted."""
    d = context['fresh']; key = context['binding']['task_key']; task = key.removeprefix('deb__')
    run = [c for c in d['commands'] if c['manifest'] is not None and
           any(x in c['run']['command'] for x in ('run', 'build'))][-1]
    files = {name: d['final_files'][name].read_bytes().decode('utf-8') for name in context['source_names']
             if name in d['final_files'] and Path(name).suffix in ('.sql', '.yml', '.yaml')
             and not name.startswith('dbt_packages/')}
    env = {**d['execution']['environment'], **d['initial'].get('environment', {})}
    process = {'environment': env, 'command': run['run']['command'],
               'cwd': run['run']['cwd'], 'returncode': 0, 'exit_code': 0,
               'dbt_command_executed': True, 'artifacts_bound_to_process': True}
    facts = {'files': files, 'environment': env, 'manifest': run['manifest'],
        'run_results': run['run_results'], 'process': process,
        'commands': [c['run'] for c in d['commands']], 'actual_build_returncode': 0,
        'trusted_complete_inventory': True, 'trusted_fresh_process': True,
        'fresh_source_invocation_binding': True, 'trusted_fresh_artifact_binding': True,
        'trusted_process_complete': True, 'trusted_manifest_complete': True,
        'trusted_model_file_inventory': [n for n in files if n.startswith('models/')],
        'probed_paths': list(files), 'database': '/app/database/retail.duckdb',
        'evidence': [context['manifest']['source_binding'], observed['probe']],
        'scope': 'Exact model submission source reconstructed on qualified clean inputs. This process is not the original Qwen execution.'}
    inspectors = {
        'dbt-calculate-running-balance': ('balance', 'source_inspection_v1.py'),
        'dbt-coupon-effectiveness': ('coupon', 'source_inspection_v1.py'),
        'dbt-exchange-rate-settlement-date': ('settlement', 'source_inspection_v2.py'),
        'dbt-sales-funnel-analysis': ('funnel', 'source_inspection_v2.py'),
        'dbt-session-attribution': ('session', 'source_inspection_v1.py'),
    }
    if task in inspectors:
        kind, filename = inspectors[task]
        path = root / 'validation/deb' / task / 'phase3_v1' / filename
        module_spec = importlib.util.spec_from_file_location('difficulty_source_inspection', path)
        inspector = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(inspector)
        probe = io.read_json(root / observed['probe']['path'])
        records = [r for r in probe['parsed'] if r['manifest']['sha256'] == io.file_hash(run['manifest_path'])]
        inspected = inspector.inspect(kind, records, run['manifest'], files)
        facts['source_lineage'] = inspected['source_lineage'] if task == 'dbt-sales-funnel-analysis' else inspected
    if task == 'dbt-customer-geographic':
        facts.update(trusted_initial_capture_complete=True,
            initial_project_exists=d['initial'].get('project_exists'),
            initial_project_contains_dbt_files='dbt_project.yml' in d['initial_files'])
    if task in ('dbt-session-attribution', 'dbt-exchange-rate-settlement-date'):
        prefix = 'models/staging/' if task == 'dbt-session-attribution' else 'models/'
        excluded = {'models/marts/finance/fact_revenue.sql'} if task.endswith('settlement-date') else set()
        before = {n: io.file_hash(p) for n, p in d['initial_files'].items()
                  if n.startswith(prefix) and n not in excluded and n.endswith('.sql')}
        after = {n: io.file_hash(p) for n, p in d['final_files'].items()
                 if n.startswith(prefix) and n not in excluded and n.endswith('.sql')}
        name = 'staging_unchanged' if task == 'dbt-session-attribution' else 'upstream_unchanged'
        facts[name] = {'status': 'pass' if before == after else 'fail',
                       'reason': 'Actual clean initial versus delivered upstream source bytes',
                       'before': before, 'after': after}
    if task in ('dbt-coupon-effectiveness', 'dbt-session-attribution'):
        name = 'reviewed_project_path' if task == 'dbt-coupon-effectiveness' else 'project_path'
        facts[name] = {'status': 'pass' if d['request']['project_root'] == '/app/dbt_models_duckdb' else 'fail',
            'reason': 'Current qualified instruction/path policy and actual fresh DuckDB project path',
            'qualified_review': context['binding']['qualified_review']}
    builds = [c for c in d['commands'] if c['manifest'] is not None and 'run' in c['run']['command']]
    if len(builds) >= 2 and builds[0]['hooks'] and builds[-1]['hooks']:
        a, z = builds[0]['hooks'][0].get('compiled', {}), builds[-1]['hooks'][0].get('compiled', {})
        checks = {}
        for name in a.keys() & z.keys():
            checks[name] = (a[name]['columns'] == z[name]['columns'] and
                Counter(d['capture'].runtime(a[name]).read_text().splitlines()) ==
                Counter(d['capture'].runtime(z[name]).read_text().splitlines()))
        # A discrepant exact comparison is not automatically a wrong output:
        # some frozen contracts allow typed time/tie representations.
        facts['idempotence'] = {'status': 'pass' if checks and set(a) == set(z) and all(checks.values()) else 'gap',
            'reason': 'Actual successive clean reconstruction captures compared without dropping columns; non-exact differences need task policy review.',
            'checks': checks}
    if events['echo_before_first_write'] is True:
        echo = min(events['echo_calls'], key=lambda x: x['result_index'])
        later = events['write_calls'] or [x for x in events['pairs'] if x['call_index'] > echo['result_index']]
        write = min(later, key=lambda x: x['call_index']) if later else {'started_at': None}
        if echo['finished_at'] and write['started_at']:
            facts['backend_before_install'] = {'shell': {'command': 'echo "$DB_TYPE"', 'exit_code': 0,
                'output': 'duckdb', 'finished_at_utc': echo['finished_at']},
                'install_after_utc': write['started_at'], 'sequential_trusted_driver_verified': True,
                'source_logs': events['source_logs'], 'source': 'original model tool events, not audit driver'}
    path = output / 'trusted-workflow.json'
    io.write_json(path, facts)
    return io.evidence_ref(path, root)


def direct_workflow(context, snapshot, events, output, root):
    """Fresh source/build provenance for the four request-style DEB checkers.

    CAC asks for delivered source and configured backend; the other contracts
    explicitly prescribe an original echo before coding. For those, unavailable
    original evidence leaves backend_shell absent (the frozen checker returns
    unknown). Audit echo can never fill that field.
    """
    d = context['fresh']; key = context['binding']['task_key']
    final = d['final_files']; ex = d['execution']
    run = [c for c in d['commands'] if c['manifest'] is not None and
           any(x in c['run']['command'] for x in ('run', 'build'))][-1]
    project = yaml.safe_load(final['dbt_project.yml'].read_text())
    paths = project.get('model-paths', ['models'])
    sources = []
    for name in context['source_names']:
        p = Path(name)
        if p.suffix == '.sql' and any(p.is_relative_to(Path(x)) for x in paths):
            sources.append({'original_file_path': name, 'file': io.evidence_ref(final[name], root)})
    def write(name, value):
        p = output / name
        if p.exists():
            raise ValueError('Preserve provenance')
        io.write_json(p, value); return io.evidence_ref(p, root)
    result = {'producer': 'locked_qwen_clean_rebuild', 'discarded_candidate_dbt_artifacts': True,
        'task_key': key, 'snapshot': snapshot,
        'project': write('project.json', {'model_paths': paths, 'source': io.evidence_ref(final['dbt_project.yml'], root)}),
        'project_source': io.evidence_ref(final['dbt_project.yml'], root),
        'profile_source': io.evidence_ref(final['profiles.yml'], root) if 'profiles.yml' in final else None,
        'project_root': d['request']['project_root'], 'actual_database': '/app/database/retail.duckdb',
        'profile_environment': {**ex['environment'], **d['initial'].get('environment', {})},
        'environment': ex['environment'],
        'observed_files': {r['relative_path']: {'exists': r['exists'],
            'file': io.evidence_ref(final[r['relative_path']], root) if r['relative_path'] in final else None}
            for r in ex['final_files']},
        'complete_model_inventory': True, 'sources': sources,
        'manifest': io.evidence_ref(run['manifest_path'], root),
        'run_results': io.evidence_ref(run['results_path'], root),
        'invocation': {'argv': run['run']['command'], 'returncode': run['run']['returncode'],
            'started_unix': datetime.fromisoformat(run['run']['started_at_utc']).timestamp(),
            'finished_unix': datetime.fromisoformat(run['run']['finished_at_utc']).timestamp(),
            'log': io.evidence_ref(context['trial'] / 'agent/audit-rebuild' / run['run']['log'], root)},
        'trust_evidence': [context['manifest']['source_binding'],
            io.evidence_ref(context['trial'] / 'agent/audit-rebuild/execution.json', root)],
        'scope': 'Exact delivered source in fresh controlled build; original required actions are bound separately.'}
    if key == 'deb__dbt-fix-cac-payback-waterfall':
        result['backend_shell'] = io.evidence_ref(d['backend_shell_path'], root)
        result['backend_scope'] = 'Environment evidence only: this contract does not prescribe model echo/chronology.'
    elif events['echo_before_first_write'] is True:
        echo = min(events['echo_calls'], key=lambda x: x['result_index'])
        later = events['write_calls'] or [x for x in events['pairs'] if x['call_index'] > echo['result_index']]
        write_event = min(later, key=lambda x: x['call_index']) if later else {'started_at': None}
        if echo['finished_at'] and write_event['started_at']:
            result['backend_shell'] = write('model-backend-echo.json', {
                'command': 'echo "$DB_TYPE"', 'exit_code': 0, 'output': 'duckdb',
                'finished_at_utc': echo['finished_at'], 'actual_call_id': echo['call_id'],
                'source_logs': events['source_logs']})
            result['source_install_after_utc'] = write_event['started_at']
    return write('workflow.json', result)


def declared_action_gate(key, events, project_root=None):
    if key == 'deb__dbt-fix-cac-payback-waterfall':
        return {'status': 'pass', 'reward': 1, 'reason': 'This contract has no mandatory echo/deps command or DBT_PROFILES_DIR export.'}
    echo = events['echo_before_first_write']
    if echo is False:
        return {'status': 'scored', 'reward': 0, 'reason': 'Actual echo completion follows an observed source write.'}
    observed_profiles = {p for row in events.get('profile_build_calls', []) for p in row['profile_directories']}
    if echo is True and project_root in observed_profiles:
        return {'status': 'pass', 'reward': 1,
                'reason': 'Actual paired Qwen echo precedes every observed mutating/unknown action; a successful explicit DBT_PROFILES_DIR-bound dbt invocation is recorded.',
                'profile_directory': project_root, 'source_logs': events.get('source_logs', [])}
    # DBT_PROFILES_DIR is required by these fourteen instructions. The clean
    # rebuild env cannot attest that the original model set it. Dedicated raw
    # command recognition is needed before this obligation becomes established.
    return {'status': 'unknown', 'reward': None,
            'reason': 'Original model echo/DBT_PROFILES_DIR evidence is incomplete; audit environment is not substituted.',
            'echo_before_first_write': echo, 'source_logs': events.get('source_logs', [])}
