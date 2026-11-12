"""Bind the frozen independent Session scorer to read-only native captures."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


core = module('session_retained_core_v1', HERE.parent / 'audit_v1/checker.py')
collector = module('session_collector_v2', HERE / 'collect.py')
read, sha, verified = core.read, core.sha, core.verified
TASK = core.TASK


def load_capture(root, reference, phase):
    path = verified(root, reference); snapshot = read(path)
    if (snapshot['schema_version'] != 1 or snapshot['phase'] != phase or snapshot['errors']
            or not snapshot['read_only_database'] or snapshot['dbt_invoked']
            or snapshot['arbitrary_submission_sql_executed']):
        raise ValueError('Invalid/failed collection phase')
    if snapshot['collector_sha256'] != sha(HERE / 'collect.py'):
        raise ValueError('Unreviewed collector version')
    db = snapshot['database_files_before']
    if db != snapshot['database_files_after'] or set(db) != {str(collector.DATABASE), str(collector.DATABASE) + '.wal'} or not db[str(collector.DATABASE)]:
        raise ValueError('Database/WAL changed during collection')
    if set(snapshot['projects']) != set(collector.PROJECTS): raise ValueError('Incomplete project inventory')
    names = {'inputs.json', 'output.json'} | {
        'projects/' + label + '/' + name for label in collector.PROJECTS for name in collector.COPIED_FILES}
    if set(snapshot['files']) != names: raise ValueError('Incomplete capture export inventory')
    for name, item in snapshot['files'].items():
        exported = (path.parent / name).resolve()
        if not exported.is_relative_to(path.parent): raise ValueError('Unsafe captured name')
        if item == {'absent': True}:
            if name in {'inputs.json', 'output.json'} or exported.exists(): raise ValueError('Invalid absence record')
        elif set(item) != {'sha256'} or not exported.is_file() or sha(exported) != item['sha256']:
            raise ValueError('Captured export changed: ' + name)
    for label, project in snapshot['projects'].items():
        if project['path'] != collector.PROJECTS[label]: raise ValueError('Unexpected project location')
        if not project['present'] and project['source_inventory']: raise ValueError('Absent project contains sources')
        for name in (*collector.CONFIG_FILES, 'models/marts/attribution_report.sql'):
            exported = snapshot['files']['projects/' + label + '/' + name]
            item = project['source_inventory'].get(name)
            if (item is None) != (exported == {'absent': True}) or item and item['sha256'] != exported.get('sha256'):
                raise ValueError('Copied source and source inventory disagree')
    output = read(path.parent / 'output.json')
    if (output is None) != (snapshot['relation_type'] is None) or output and output['relation_type'] != snapshot['relation_type']:
        raise ValueError('Captured relation and output disagree')
    return path.parent, snapshot


def normalized(output):
    if output is None: return None
    return {**output, 'rows': sorted(output['rows'], key=collector.encoded)}


def workflow(root, observation, captured_dir, snapshot, output, expected):
    if observation is None:
        return {'status': 'unknown', 'issues': [], 'unknown': ['No reviewed workflow observation; project-path policy remains unresolved']}
    if observation['kind'] != 'reviewed_validation_case_snapshots': raise ValueError('Unreviewed workflow capture')
    inputs = verified(root, observation['inputs'])
    if sha(inputs) != sha(captured_dir / 'inputs.json'): raise ValueError('Workflow input differs from captured submission')
    before = read(verified(root, observation['before']))
    phases = [read(verified(root, p)) for p in observation['phases']]
    if not phases or normalized(phases[-1]['required_output']) != normalized(output):
        raise ValueError('Workflow output differs from captured submission')
    project = snapshot['projects']['native']
    final = phases[-1]
    model = project['source_inventory'].get('models/marts/attribution_report.sql')
    if final['model_present'] != (model is not None) or final['model_sha256'] != (model or {}).get('sha256'):
        raise ValueError('Workflow model differs from captured submission')
    staging = {k: v['sha256'] for k, v in project['source_inventory'].items() if k.startswith('models/staging/')}
    if final['staging_sources'] != staging: raise ValueError('Workflow staging inventory differs')
    for name in ['target/manifest.json', 'target/run_results.json']:
        evidence = verified(root, observation[name])
        if sha(evidence) != sha(captured_dir / 'projects/native' / name):
            raise ValueError('Workflow invocation differs from captured submission')
    return core.workflow(before, phases, expected)


def score(root, request_path):
    request = read(request_path)
    if request['schema_version'] != 2 or request['task_key'] != TASK: raise ValueError('Unexpected captured audit schema/task')
    before_dir, before = load_capture(root, request['before'], 'before-native')
    after_dir, after = load_capture(root, request['after'], 'after-native')
    if (before['database_files_after'] != after['database_files_before'] or before['projects'] != after['projects']
            or before['files'] != after['files'] or before['relation_type'] != after['relation_type']):
        raise ValueError('Native verifier or capture changed the submission; independent-copy review required')
    inputs = before_dir / 'inputs.json'
    policy = read(HERE.parent / 'audit_v1/input-policy.json')['input_profiles'].get(sha(inputs))
    if policy is None or policy['data_scope'] != request['data_scope']: raise ValueError('Unregistered input identity/scope')
    reconstruction = core.recompute(read(inputs)); output = read(before_dir / 'output.json')
    data = core.compare(reconstruction['expected'], output)
    if reconstruction['unknown']:
        data = {'status': 'unknown', 'issues': [], 'unknown': reconstruction['unknown']}
    observed = workflow(root, request.get('workflow'), before_dir, before, output, reconstruction['expected'])
    if before['relation_type'] not in {None, 'BASE TABLE'}:
        observed = {**observed, 'status': 'fail', 'issues': [*observed['issues'], 'table_materialization_required']}
    statuses = {data['status'], observed['status']}
    status = 'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass'
    return dict(schema_version=2, task_key=TASK, status=status,
        audit_reward={'pass': 1.0, 'fail': 0.0, 'unknown': None}[status], data=data, workflow=observed,
        coverage=reconstruction['coverage'], data_scope=request['data_scope'], input_profile=policy['id'],
        input_manifest_sha256=sha(request_path), checker_sha256=sha(Path(__file__)),
        core_sha256=sha(HERE.parent / 'audit_v1/checker.py'), policy_sha256=sha(HERE.parent / 'audit_v1/input-policy.json'),
        collector_sha256=sha(HERE / 'collect.py'), certified=False, native_reward_read_or_modified=False,
        submission_unchanged_during_native=True, missing_output=output is None,
        scope='Declared read-only before/after-native capture; complete production workflow and conflicting project-path/boundary policies remain pending.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=HERE.parents[3])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Preserve existing audit result')
    try: result = score(args.root.resolve(), args.manifest.resolve())
    except (ValueError, KeyError, OSError, TypeError, ArithmeticError) as exc:
        result = {'status': 'error', 'audit_reward': None, 'certified': False, 'error': str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print({k: result[k] for k in ['status', 'audit_reward', 'certified']})
    raise SystemExit(0 if result['status'] in {'pass', 'fail'} else 1)
