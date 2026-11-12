"""Experimental independent session audit; never reads native rewards or case validity.

Weights are scaled relative to the newest eligible touch, aggregated by channel,
then normalized. This cancels the common conversion-time decay factor and is
independent of the case proof's per-touch conversion-age implementation.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = 'deb__dbt-session-attribution'
REQUIRED = ('channel', 'total_conversions', 'total_revenue')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    def invalid(value):
        raise ValueError('Non-finite JSON token: ' + value)
    return json.loads(Path(path).read_text(), parse_constant=invalid)


def decimal(value):
    if type(value) not in {str, int, float, Decimal}:
        raise ValueError('Non-numeric value')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite numeric value')
    return result


def classify(row):
    medium = (row['utm_medium'] or '').lower()
    referral = (row['referrer'] or '').lower()
    predicates = [
        ('Paid Search', medium in {'cpc', 'ppc'}),
        ('Paid Social', medium == 'paid_social' or 'facebook' in referral and row['utm_source'] not in {None, ''}),
        ('Organic Search', any(s in referral for s in ['google', 'bing', 'yahoo'])),
        ('Organic Social', any(s in referral for s in ['facebook', 'twitter', 'linkedin', 'instagram'])),
        ('Email', medium == 'email' or 'mail' in referral),
        ('Direct', referral == ''),
        ('Referral', True),
    ]
    return next(label for label, matches in predicates if matches)


def recompute(inputs):
    sessions = inputs['sessions']
    stamps, candidates = {}, defaultdict(list)
    revenue = defaultdict(Decimal)
    uncertain, counts = [], Counter()
    for s in sessions:
        key = s['session_id']
        if type(key) is not str or key in stamps or type(s['is_converted']) is not bool:
            raise ValueError('Unexpected session key/boolean representation')
        stamps[key] = datetime.fromisoformat(s['session_start'])
        for col in ['customer_id', 'order_id']:
            if s[col] is not None and type(s[col]) is not str:
                raise ValueError('Input identifiers must remain exact strings')
        if s['duration_seconds'] is None or s['page_views'] is None:
            uncertain.append({'kind': 'null_quality_metric', 'session_id': key})
            continue
        if s['duration_seconds'] < 5 or s['page_views'] < 2 or (s['device_type'] or '').lower() == 'bot':
            continue
        if s['device_type'] is None:
            uncertain.append({'kind': 'null_device', 'session_id': key})
        if s['customer_id'] is not None:
            candidates[s['customer_id']].append(s)
    with localcontext() as context:
        context.prec = 80
        for sale in inputs['sales']:
            if sale['order_id'] is None:
                continue
            if type(sale['order_id']) is not str:
                raise ValueError('Sales order ID type differs')
            if sale['total_amount'] is None:
                uncertain.append({'kind': 'null_sale_amount', 'order_id': sale['order_id']})
            else:
                revenue[sale['order_id']] += decimal(sale['total_amount'])
        conversions = [s for s in sessions if s['is_converted'] and s['order_id'] in revenue]
        for order, n in Counter(s['order_id'] for s in conversions).items():
            if n > 1:
                uncertain.append({'kind': 'repeated_converting_order', 'order_id': order})
        sums = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for conversion in conversions:
            end = stamps[conversion['session_id']]
            eligible = sorted((s for s in candidates.get(conversion['customer_id'], [])
                               if end - timedelta(days=14) <= stamps[s['session_id']] < end),
                              key=lambda s: stamps[s['session_id']])
            counts['conversions'] += 1
            counts['touches_' + str(len(eligible))] += 1
            if not eligible:
                continue
            if len({stamps[s['session_id']] for s in eligible}) != len(eligible):
                uncertain.append({'kind': 'tied_touch', 'conversion_session': conversion['session_id']})
            anchor = stamps[eligible[-1]['session_id']]
            by_channel = defaultdict(Decimal)
            for i, s in enumerate(eligible):
                age = anchor - stamps[s['session_id']]
                micros = (age.days * 86400 + age.seconds) * 1000000 + age.microseconds
                coefficient = Decimal(1)
                if len(eligible) > 1:
                    coefficient = Decimal('1.5') if i == 0 else Decimal('1.3') if i == len(eligible) - 1 else Decimal(1)
                by_channel[classify(s)] += coefficient * (-Decimal(micros) / Decimal(604800000000)).exp()
            denominator = sum(by_channel.values())
            amount = revenue[conversion['order_id']] * (Decimal('1.1') if len(by_channel) >= 3 else Decimal(1))
            counts['bonus_paths'] += int(len(by_channel) >= 3)
            for channel, weight in by_channel.items():
                sums[channel][0] += weight / denominator
                sums[channel][1] += amount * weight / denominator
        expected = []
        for channel, values in sorted(sums.items()):
            for column, value in zip(REQUIRED[1:], values):
                if abs(abs(value) % Decimal('.01') - Decimal('.005')) < Decimal('1e-60'):
                    uncertain.append({'kind': 'half_cent_rounding_unspecified', 'channel': channel, 'column': column})
            expected.append(dict(channel=channel, **{c: str(v.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                                                    for c, v in zip(REQUIRED[1:], values)}))
    return {'expected': expected, 'unknown': uncertain, 'coverage': dict(counts)}


def compare(expected, output):
    if output is None:
        return {'status': 'fail', 'issues': [{'kind': 'missing_required_relation'}], 'unknown': []}
    issues = []
    columns = output['columns']
    if len(set(columns)) != len(columns) or not set(REQUIRED).issubset(columns):
        return {'status': 'fail', 'issues': [{'kind': 'missing_or_duplicate_required_columns'}], 'unknown': []}
    wanted = {r['channel']: r for r in expected}
    seen = Counter()
    for row in output['rows']:
        label = row.get('channel')
        if type(label) is not str:
            issues.append({'kind': 'invalid_channel_key'})
            continue
        seen[label] += 1
        if label not in wanted:
            continue
        for col in REQUIRED[1:]:
            try:
                actual = decimal(row.get(col))
            except (ValueError, ArithmeticError):
                issues.append({'kind': 'invalid_numeric_value', 'channel': label, 'column': col})
                continue
            if abs(actual - decimal(wanted[label][col])) > Decimal('0.000000001'):
                issues.append({'kind': 'value', 'channel': label, 'column': col,
                               'expected': wanted[label][col], 'actual': str(actual)})
    issues.extend({'kind': 'duplicate_channel', 'channel': k} for k, n in seen.items() if n > 1)
    issues.extend({'kind': 'missing_channel', 'channel': k} for k in sorted(set(wanted) - set(seen)))
    issues.extend({'kind': 'extra_channel', 'channel': k} for k in sorted(set(seen) - set(wanted)))
    return {'status': 'fail' if issues else 'pass', 'issues': issues, 'unknown': []}


def workflow(before, phases, expected):
    if before is None or not phases:
        return {'status': 'unknown', 'issues': [], 'unknown': ['No reviewed workflow observation']}
    final = phases[-1]
    issues, unknown = [], []
    if not final['model_present']:
        issues.append('required_model_absent')
    output = final['required_output']
    if output is None:
        issues.append('required_relation_absent')
    elif output['relation_type'] != 'BASE TABLE':
        issues.append('table_materialization_required')
    added = set(final['staging_sources']) - set(before['staging_sources'])
    if added:
        issues.append('new_staging_models')
    if final['query_channels'] != sorted(r['channel'] for r in expected):
        issues.append('compiled_query_not_ascending')
    if len(phases) < 2:
        unknown.append('No observed unchanged-source repeat build')
    elif phases[-2]['model_sha256'] != final['model_sha256']:
        unknown.append('Repeat builds do not share the same model bytes')
    elif any(compare(expected, p['required_output'])['status'] != 'pass' for p in phases[-2:]):
        issues.append('repeated_build_not_stable_and_correct')
    return {'status': 'fail' if issues else 'unknown' if unknown else 'pass', 'issues': issues, 'unknown': unknown}


def verified(root, reference):
    if set(reference) != {'path', 'sha256'}:
        raise ValueError('Unexpected artifact reference')
    path = (root / reference['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha(path) != reference['sha256']:
        raise ValueError('Missing, changed or outside-root evidence')
    return path


def score(root, request_path):
    request = read(request_path)
    if request['schema_version'] != 1 or request['task_key'] != TASK:
        raise ValueError('Unknown session audit manifest')
    if request['output_kind'] not in {'workflow_snapshot', 'semantic_snapshot'}:
        raise ValueError('Unreviewed output capture schema')
    inputs = verified(root, request['inputs'])
    policy = read(HERE / 'input-policy.json')['input_profiles'].get(sha(inputs))
    if policy is None or policy['data_scope'] != request['data_scope']:
        raise ValueError('Unregistered input profile/scope')
    reconstructed = recompute(read(inputs))
    output_document = read(verified(root, request['output']))
    output = output_document['required_output'] if request['output_kind'] == 'workflow_snapshot' else output_document
    data = compare(reconstructed['expected'], output)
    if reconstructed['unknown']:
        data = {'status': 'unknown', 'issues': [], 'unknown': reconstructed['unknown']}
    before, phases = None, []
    if request.get('workflow'):
        capture = request['workflow']
        if capture['kind'] != 'reviewed_validation_case_snapshots':
            raise ValueError('Unreviewed workflow capture kind')
        before = read(verified(root, capture['before']))
        phases = [read(verified(root, r)) for r in capture['phases']]
        if phases[-1] != output_document:
            raise ValueError('Workflow and scored submission are not identical')
    observed = workflow(before, phases, reconstructed['expected'])
    statuses = {data['status'], observed['status']}
    status = 'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass'
    return {'schema_version': 1, 'task_key': TASK, 'status': status,
        'audit_reward': {'pass': 1.0, 'fail': 0.0, 'unknown': None}[status],
        'data': data, 'workflow': observed, 'coverage': reconstructed['coverage'],
        'input_profile': policy['id'], 'data_scope': request['data_scope'],
        'checker_sha256': sha(Path(__file__)), 'policy_sha256': sha(HERE / 'input-policy.json'),
        'input_manifest_sha256': sha(request_path), 'certified': False, 'native_reward_read_or_modified': False,
        'scope': 'Experimental retained-capture audit. Production capture, project-path discrepancy and complete protocol remain unreviewed; successful components do not certify a task or sample.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=HERE.parents[3])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():raise ValueError('Preserve existing audit result')
    try:
        result = score(args.root.resolve(), args.manifest.resolve())
    except (ValueError, KeyError, OSError, TypeError, ArithmeticError) as exc:
        result = {'status': 'error', 'audit_reward': None, 'certified': False, 'error': str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print({k: result[k] for k in ['status', 'audit_reward', 'certified']})
    raise SystemExit(0 if result['status'] in {'pass', 'fail'} else 1)
