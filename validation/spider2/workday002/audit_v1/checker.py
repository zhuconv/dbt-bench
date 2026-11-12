"""Experimental, read-only Workday audit over explicitly captured artifacts.

No gold, native reward, case verdict or case-proof implementation is imported.
The CLI accepts only pinned input profiles. Workflow observations are supplied
by a separately reviewed capture; absent observations remain unknown. This
version is not certified for arbitrary model traces or unreviewed collectors.
"""
import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = 'spider2__workday002'
CORE = {'job_profile_id': 'id', 'job_profile_code': 'job_profile_code', 'job_title': 'title',
        'private_title': 'private_title', 'job_summary': 'summary', 'job_description': 'description'}
AGGREGATES = ('job_family_codes', 'job_family_summaries', 'job_family_group_codes', 'job_family_group_summaries')
REQUIRED = (*CORE, 'source_relation', *AGGREGATES)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    def nonfinite(token):
        raise ValueError('Non-finite JSON value in export: ' + token)
    return json.loads(Path(path).read_text(), parse_constant=nonfinite)


def expected_rows(inputs):
    """Reverse-indexed set propagation; independent of case nested-loop proof."""
    def active(name):
        rows = inputs['workday_' + name + '_data']['rows']
        for row in rows:
            flag = row['_fivetran_deleted']
            if flag is not None and type(flag) is not bool:
                raise ValueError('Unexpected deletion flag type')
        return [r for r in rows if r['_fivetran_deleted'] is not True]

    def text(value):
        if value is not None and not isinstance(value, str):
            raise ValueError('Unexpected source string type')
        return set() if value is None else {value}

    groups = defaultdict(lambda: [set(), set()])
    for group in active('job_family_group'):
        if group['id'] is not None:
            groups[group['id']][0].update(text(group['job_family_group_code']))
            groups[group['id']][1].update(text(group['summary']))
    bridges = defaultdict(set)
    for bridge in active('job_family_job_family_group'):
        if bridge['job_family_id'] is not None and bridge['job_family_group_id'] is not None:
            bridges[bridge['job_family_id']].add(bridge['job_family_group_id'])
    families = defaultdict(lambda: [set(), set(), set(), set()])
    for family in active('job_family'):
        family_id = family['id']
        if family_id is None:
            continue
        values = families[family_id]
        values[0].update(text(family['job_family_code']))
        values[1].update(text(family['summary']))
        for group_id in bridges[family_id]:
            values[2].update(groups[group_id][0])
            values[3].update(groups[group_id][1])
    by_profile = defaultdict(lambda: [set(), set(), set(), set()])
    for link in active('job_family_job_profile'):
        if link['job_profile_id'] is None or link['job_family_id'] is None:
            continue
        for index, values in enumerate(families[link['job_family_id']]):
            by_profile[link['job_profile_id']][index].update(values)
    result = []
    seen = set()
    for profile in active('job_profile'):
        key = profile['id']
        if not isinstance(key, str) or key in seen:
            raise ValueError('Unexpected null/non-string/duplicate input profile key')
        seen.add(key)
        result.append({**{c: profile[s] for c, s in CORE.items()}, 'source_relation': '',
                       **{c: sorted(v) for c, v in zip(AGGREGATES, by_profile[key])}})
    return sorted(result, key=lambda r: r['job_profile_id'])


def aggregate_verdict(actual, members):
    if not members:
        if actual is None or type(actual) is list and actual == []:
            return 'pass'
        if actual == '':
            return 'unknown'
        return 'fail'
    if type(actual) is list:
        return 'pass' if all(type(x) is str for x in actual) and Counter(actual) == Counter(members) else 'fail'
    if type(actual) is not str:
        return 'fail'
    expected = tuple(sorted(members))
    if any(type(x) is not str for x in expected) or len(set(expected)) != len(expected):
        raise ValueError('Invalid expected aggregate membership')

    # Match complete members, including embedded delimiters. No split/round/zip.
    @lru_cache(None)
    def match(remainder, available):
        if not available:
            return remainder == ''
        for i in available:
            rest = tuple(j for j in available if j != i)
            prefix = expected[i] + (', ' if rest else '')
            if remainder.startswith(prefix) and match(remainder[len(prefix):], rest):
                return True
        return False
    return 'pass' if match(actual, tuple(range(len(expected)))) else 'fail'


def compare_rows(expected, output):
    columns = [c['name'] for c in output['columns']]
    issues, unknown = [], []
    if len(set(columns)) != len(columns):
        return {'status': 'fail', 'issues': [{'kind': 'duplicate_columns'}], 'unknown': []}
    missing = set(REQUIRED) - set(columns)
    if missing:
        return {'status': 'fail', 'issues': [{'kind': 'missing_columns', 'columns': sorted(missing)}], 'unknown': []}
    expected_map = {(r['source_relation'], r['job_profile_id']): r for r in expected}
    actual_map = {}
    for row in output['rows']:
        if not set(REQUIRED).issubset(row):
            issues.append({'kind': 'incomplete_row'})
            continue
        key = row['source_relation'], row['job_profile_id']
        if not all(type(k) is str for k in key):
            issues.append({'kind': 'invalid_key_type'})
            continue
        if key in actual_map:
            issues.append({'kind': 'duplicate_key', 'key': key})
        actual_map[key] = row
    issues += [{'kind': 'missing_row', 'key': k} for k in sorted(expected_map.keys() - actual_map.keys())]
    issues += [{'kind': 'extra_row', 'key': k} for k in sorted(actual_map.keys() - expected_map.keys())]
    for key in sorted(expected_map.keys() & actual_map.keys()):
        gold, actual = expected_map[key], actual_map[key]
        for column in REQUIRED:
            left, right = gold[column], actual[column]
            if column in AGGREGATES:
                verdict = aggregate_verdict(right, left)
            else:
                verdict = 'pass' if type(left) is type(right) and left == right else 'fail'
            if verdict == 'fail':
                issues.append({'kind': 'value', 'key': key, 'column': column, 'expected': left, 'actual': right})
            elif verdict == 'unknown':
                unknown.append({'kind': 'empty_string_policy_unresolved', 'key': key, 'column': column})
    return {'status': 'fail' if issues else 'unknown' if unknown else 'pass', 'issues': issues, 'unknown': unknown}


def workflow_verdict(observation, run_results, manifest, required_ids):
    if observation is None:
        return {'status': 'unknown', 'issues': [], 'unknown': ['No reviewed workflow observation capture']}
    issues, unknown = [], []
    # These command observations must be bound by the caller to reviewed capture.
    commands = observation['commands']
    good = [c['argv'] for c in commands if c['returncode'] == 0]
    if observation['packages_present'] and not any(c[:2] == ['dbt', 'deps'] for c in good):
        issues.append('deps_not_run')
    if not observation['target_model_present']:
        issues.append('target_model_missing')
    if observation['relation_type'] != 'BASE TABLE':
        issues.append('target_is_not_table')
    if not any(c[:2] == ['dbt', 'run'] for c in good):
        issues.append('dbt_run_not_executed')
    if run_results is None or manifest is None:
        unknown.append('Missing dbt invocation artifacts')
    else:
        if run_results['args']['which'] != 'run':
            issues.append('not_a_dbt_run')
        if run_results['metadata']['invocation_id'] != manifest['metadata']['invocation_id']:
            issues.append('invocation_mismatch')
        enabled = {key for key, node in manifest['nodes'].items()
                   if node['resource_type'] == 'model' and node['config']['enabled']}
        observed = [r['unique_id'] for r in run_results['results']]
        if enabled != set(required_ids):
            issues.append('required_models_disabled_or_changed')
        if Counter(observed) != Counter(required_ids):
            issues.append('all_models_not_executed')
        if any(r['status'] != 'success' for r in run_results['results']):
            issues.append('model_run_not_successful')
    return {'status': 'fail' if issues else 'unknown' if unknown else 'pass', 'issues': issues, 'unknown': unknown}


def combine(data, workflow):
    statuses = {data['status'], workflow['status']}
    status = 'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass'
    return {'status': status, 'audit_reward': {'pass': 1.0, 'fail': 0.0, 'unknown': None}[status],
            'data': data, 'workflow': workflow}


def verified(root, reference):
    if set(reference) != {'path', 'sha256'}:
        raise ValueError('Unexpected artifact reference')
    path = (root / reference['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha(path) != reference['sha256']:
        raise ValueError('Missing, changed or outside-root artifact: ' + reference['path'])
    return path


def score(root, manifest_path):
    request = read(manifest_path)
    policy = read(HERE / 'input-policy.json')
    if request['schema_version'] != 1 or request['task_key'] != TASK:
        raise ValueError('Wrong audit input manifest')
    inputs_path, output_path = (verified(root, request[k]) for k in ['inputs', 'output'])
    input_profile = policy['input_profiles'].get(sha(inputs_path))
    if input_profile is None or input_profile['data_scope'] != request['data_scope']:
        raise ValueError('Input data not in the explicit fixed-input/fixture registry')
    observation = rr = dbt_manifest = None
    if request.get('workflow'):
        w = request['workflow']
        if w.get('capture_kind') != 'reviewed_validation_case_command_capture':
            raise ValueError('Unreviewed workflow capture kind')
        # Hashes attest exact captured evidence; production capture still needs
        # independent certification. Case verdicts are never consumed here.
        verified(root, w['executed_engine'])
        observation = read(verified(root, w['observation']))
        if observation['output_sha256'] != sha(output_path):
            raise ValueError('Workflow and output snapshots differ')
        for command in observation['commands']:
            log = verified(root, w['command_logs'][command['log']])
            if sha(log) != command['log_sha256']:
                raise ValueError('Command log binding differs')
        rr = read(verified(root, w['run_results'])) if w.get('run_results') else None
        dbt_manifest = read(verified(root, w['dbt_manifest'])) if w.get('dbt_manifest') else None
    data = compare_rows(expected_rows(read(inputs_path)), read(output_path))
    workflow = workflow_verdict(observation, rr, dbt_manifest, policy['required_model_ids'])
    return {**combine(data, workflow), 'schema_version': 1, 'task_key': TASK, 'data_scope': request['data_scope'],
            'input_profile': input_profile['id'], 'input_manifest_sha256': sha(manifest_path),
            'checker_sha256': sha(Path(__file__)), 'policy_sha256': sha(HERE / 'input-policy.json'),
            'certified': False, 'native_reward_read_or_modified': False,
            'scope': 'Experimental retained-artifact audit. Production collector and full protocol are not certified; empty-string ambiguity and missing workflow evidence stay unknown.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=HERE.parents[3])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve existing audit output')
    try:
        result = score(args.root.resolve(), args.manifest.resolve())
    except (ValueError, KeyError, OSError, TypeError) as exc:
        result = {'status': 'error', 'audit_reward': None, 'certified': False, 'error': str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
    print({k: result[k] for k in ['status', 'audit_reward', 'certified']})
    return 0 if result['status'] in {'pass', 'fail'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
