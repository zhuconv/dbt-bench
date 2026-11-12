"""F1003 diagnostic audit; trusted harness captures, independent raw expectations.

CLI: python check.py --request request.json --output result.json --repo-root REPO
The request and capture references are harness-owned, never candidate-supplied.
No candidate SQL fingerprint is prescribed. No native verifier is invoked here.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from datetime import datetime
from dataclasses import replace


def read_ref(ref, root):
    data = (root / ref['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('Capture hash mismatch: ' + ref['path'])
    return data


def workflow_check(ref, snapshot_ref, task_key, root):
    """A successful captured build must bind the current source and dbt node.

    File hashes only bind observed artifacts to one invocation. They never
    choose which SQL implementation is accepted. Failed or missing invocation
    evidence is unknown; an observed absence of the requested model is wrong.
    """
    data = json.loads(read_ref(ref, root))
    if data.get('producer') not in ('task_image_selftest_clean_build', 'locked_qwen_clean_rebuild') or data.get('discarded_candidate_dbt_artifacts') is not True:
        raise ValueError('Workflow requires a trusted fresh build, not candidate dbt artifacts')
    if data['task_key'] != task_key or data['snapshot'] != snapshot_ref:
        raise ValueError('Workflow is not bound to this task and snapshot')
    project = json.loads(read_ref(data['project'], root))
    model_paths = project['model_paths']
    sources = data['sources']
    matches = []
    for item in sources:
        path = PurePosixPath(item['original_file_path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid captured model path')
        if path.name == 'analysis__answer.sql' and any(
                path.is_relative_to(PurePosixPath(p)) for p in model_paths):
            matches.append(item)
    if data.get('complete_model_inventory') is not True:
        raise ValueError('Model inventory was not captured completely')
    if not matches:
        return {'status': 'scored', 'reward': 0.0, 'checks': {'model_source': False},
                'reason': 'Complete configured-model-path inventory has no analysis__answer.sql'}
    if len(matches) != 1:
        return {'status': 'scored', 'reward': 0.0, 'checks': {'unique_model_source': False},
                'reason': 'Multiple model sources have the required dbt model name'}
    source = matches[0]
    raw_code = read_ref(source['file'], root).decode()
    if not raw_code.strip():
        return {'status': 'scored', 'reward': 0.0, 'checks': {'nonempty_source': False},
                'reason': 'The requested model file is empty'}
    invocation = data['invocation']
    if invocation['returncode'] != 0:
        raise ValueError('Captured build did not succeed; inspect build failure separately')
    argv = invocation['argv']
    if not any(v in ('run', 'build') for v in argv):
        raise ValueError('No captured dbt materialization command')
    manifest = json.loads(read_ref(data['manifest'], root))
    results = json.loads(read_ref(data['run_results'], root))
    if manifest['metadata']['invocation_id'] != results['metadata']['invocation_id']:
        raise ValueError('dbt manifest/run_results invocation mismatch')
    for artifact in (manifest, results):
        generated = datetime.fromisoformat(artifact['metadata']['generated_at'].replace('Z', '+00:00')).timestamp()
        if not invocation['started_unix'] - 1 <= generated <= invocation['finished_unix'] + 1:
            raise ValueError('dbt artifact timestamp is outside the observed build invocation')
    nodes = [(key, node) for key, node in manifest['nodes'].items()
             if node.get('resource_type') == 'model' and node.get('name') == 'analysis__answer']
    if len(nodes) != 1:
        raise ValueError('Requested model node not uniquely captured in manifest')
    key, node = nodes[0]
    # dbt's reader strips leading/trailing whitespace before storing raw_code.
    if node['original_file_path'] != source['original_file_path'] or node['raw_code'] != raw_code.strip():
        raise ValueError('Captured source differs from the dbt invocation source')
    runs = [v for v in results['results'] if v['unique_id'] == key]
    if len(runs) != 1 or runs[0]['status'] != 'success':
        raise ValueError('No successful materialization result for this model node')
    checks = {'model_source': True, 'source_bound_to_invocation': True,
              'successful_materialization': True,
              'relation_schema': node['schema'].casefold() == 'main',
              'relation_alias': node['alias'].casefold() == 'analysis__answer',
              'materialization': node['config']['materialized'] in ('table', 'view', 'incremental')}
    # Incremental is legitimate if its observed current table passes all rows.
    return {'status': 'scored', 'reward': float(all(checks.values())), 'checks': checks,
            'reason': 'Actual captured dbt invocation binds current model source to materialized answer',
            'unique_id': key, 'invocation_id': manifest['metadata']['invocation_id']}


def run(request, root):
    sys.path[:0] = [str(root / 'scripts'), str(root / 'src')]
    from audit_tables import TablePolicy, load_observer_table, audit_table
    from dbt_bench.comparison import Column, TableContract
    task_key = request['task_key']
    if task_key not in ('ade__ade-bench-f1003', 'ade__ade-bench-f1003-hard'):
        raise ValueError('Wrong task family')
    expected_path = Path(__file__).with_name('expected.json')
    expected = json.loads(expected_path.read_bytes())
    names = expected['other_inconsistent_models']
    policy = TablePolicy(TableContract((Column('answer', 'text'),), keys=('answer',)),
                         sql_types={'answer': ('VARCHAR',)}, not_null=('answer',),
                         relation_types=('BASE TABLE', 'VIEW'))
    components = {}
    try:
        candidate = load_observer_table(request['snapshot'], task_key=task_key,
                    database='/app/f1.duckdb', relation='main.analysis__answer',
                    stage=request['stage'], root=root)
        # DuckDB identifiers are case-insensitive even when quoted. Values are
        # exact filename stems and are never case-folded or substring matched.
        if len({n.casefold() for n in candidate.physical_columns}) == len(candidate.physical_columns):
            candidate = replace(candidate,
                columns=tuple(n.casefold() for n in candidate.columns),
                sql_types={n.casefold(): t for n, t in candidate.sql_types.items()},
                rows=[{n.casefold(): v for n, v in row.items()} for row in candidate.rows],
                physical_columns=tuple(n.casefold() for n in candidate.physical_columns),
                physical_sql_types={n.casefold(): t for n, t in candidate.physical_sql_types.items()})
        interface = audit_table(candidate, policy)
        actual = [row.get('answer') for row in candidate.rows]
        # This ambiguity is inherited from the task wording/reference, not
        # relaxed substring, NULL, duplicate, type or extra-column matching.
        optional_given = (interface['reward'] == 1.0 and len(actual) == len(names) + 1
                          and set(actual) == set(names) | {expected['given_inconsistent_model']})
        if optional_given:
            table = dict(interface, status='unknown', reward=None,
                         reason='Exactly the three others plus given example; optional example inclusion remains unresolved')
        else:
            table = audit_table(candidate, policy, [{'answer': name} for name in names])
        components['table'] = table
    except Exception as exc:
        components['table'] = {'status': 'unknown', 'reward': None, 'reason': str(exc)}
    try:
        components['workflow'] = workflow_check(request['workflow'], request['snapshot'], task_key, root)
    except Exception as exc:
        components['workflow'] = {'status': 'unknown', 'reward': None, 'reason': str(exc)}
    rewards = [v['reward'] for v in components.values()]
    reward = 0.0 if 0.0 in rewards else None if None in rewards else 1.0
    code_paths = [Path(__file__), Path(__file__).with_name('derive_expected.py'),
                  expected_path, root / 'scripts/audit_tables.py',
                  root / 'src/dbt_bench/comparison.py']
    code_refs = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in code_paths]
    return {'schema_version': 1, 'task_key': task_key, 'target': 'audit',
            'status': 'unknown' if reward is None else 'scored', 'reward': reward,
            'components': components, 'code_refs': code_refs, 'evidence': {'snapshot': request['snapshot'],
            'workflow': request['workflow'], 'expected_sha256': hashlib.sha256(expected_path.read_bytes()).hexdigest()},
            'scope': 'Fixed original inputs: exact diagnostic row set/interface plus actual captured dbt model workflow; no native-score modification'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repo-root', type=Path, required=True)
    args = p.parse_args()
    try:
        result = run(json.loads(args.request.read_bytes()), args.repo_root)
    except Exception as exc:
        result = {'status': 'unknown', 'reward': None, 'reason': str(exc), 'target': 'audit'}
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
