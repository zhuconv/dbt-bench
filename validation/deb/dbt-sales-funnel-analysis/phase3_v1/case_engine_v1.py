"""Explicit variant cases after the blind full build; no grader-based validity."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess


MODELS = ('int_session_funnel', 'funnel_conversion_rates', 'funnel_dropoff_analysis')
SCHEMA = 'funnel_analytics'
RAW = {'sessions': 'main.SESSIONS', 'events': 'main.EVENTS', 'carts': 'main.SHOPPING_CARTS'}


def trim(value):
    return value.strip() if isinstance(value, str) else value


def encoded(value):
    if isinstance(value, datetime):
        return {'$type': 'datetime', 'value': str(value)}
    if isinstance(value, date):
        return {'$type': 'date', 'value': str(value)}
    if isinstance(value, Decimal):
        return {'$type': 'decimal', 'value': str(value)}
    if isinstance(value, Fraction):
        return {'$type': 'fraction', 'value': str(value)}
    raise TypeError(type(value).__name__)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=encoded).encode()).hexdigest()


def rows(connection, relation):
    cursor = connection.execute('SELECT * FROM ' + relation)
    columns = [column[0].lower() for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def derive(raw):
    """Independent Python recomputation under reviewed blind interpretation."""
    events = defaultdict(set)
    for row in raw['events']:
        sid = trim(row['session_id'])
        kind = str(trim(row['event_type']) or '').lower()
        name = str(trim(row['event_name']) or '').lower()
        if kind == 'view_product' or (kind == 'v' and name == 'view product'):
            events[sid].add('view')
        if kind in ('add_to_cart', 'a'):
            events[sid].add('cart')
    converted = {trim(row['session_id']) for row in raw['carts']
                 if str(trim(row['status']) or '').upper() == 'CONVERTED'
                 or row['order_id'] is not None}
    sessions = []
    for row in raw['sessions']:
        sid = trim(row['session_id'])
        assert sid, 'Null/blank population policy requires separate review'
        view, cart = int('view' in events[sid]), int('cart' in events[sid])
        purchase = int(str(trim(row['is_converted']) or '').upper() in ('TRUE', 'T', 'YES', 'Y', '1')
                       or sid in converted)
        stamp = trim(row['session_start'])
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp)
            except ValueError:
                stamp = None
        sessions.append({'session_id': sid, 'visitor_id': trim(row['visitor_id']) or None,
                         'customer_id': trim(row['customer_id']) or None, 'session_start': stamp,
                         'has_product_view': view, 'has_add_to_cart': cart, 'has_purchase': purchase,
                         'funnel_stage': 'PURCHASE' if purchase else 'CART' if cart else 'VIEW' if view else 'NONE'})
    assert len({row['session_id'] for row in sessions}) == len(sessions)
    n = len(sessions)
    v, a, p = (sum(row[name] for row in sessions)
               for name in ('has_product_view', 'has_add_to_cart', 'has_purchase'))
    ratio = lambda x, y: Fraction(x, y) if y else None
    summary = [{'total_sessions': n, 'sessions_with_view': v, 'sessions_with_cart': a,
                'sessions_with_purchase': p, 'view_rate': ratio(v, n), 'view_to_cart_rate': ratio(a, v),
                'cart_to_purchase_rate': ratio(p, a), 'overall_conversion_rate': ratio(p, n)}]
    counts = [('BEFORE_VIEW', sum(not row['has_product_view'] for row in sessions)),
              ('VIEW_TO_CART', sum(row['has_product_view'] and not row['has_add_to_cart'] for row in sessions)),
              ('CART_TO_PURCHASE', sum(row['has_add_to_cart'] and not row['has_purchase'] for row in sessions))]
    drops = [{'dropoff_stage': label, 'session_count': count, 'dropoff_rate': ratio(count, n)}
             for label, count in counts]
    return dict(zip(MODELS, (sessions, summary, drops)))


def compare(expected, actual):
    """Check all stated cells; integer ratios allow at most one final DOUBLE ulp."""
    failures = []
    for model in MODELS:
        wanted, got = expected[model], actual[model]
        key = 'session_id' if model == MODELS[0] else 'dropoff_stage' if model == MODELS[2] else None
        if len(wanted) != len(got):
            failures.append({'model': model, 'reason': 'row count'})
            continue
        if key:
            if len({row.get(key) for row in got}) != len(got):
                failures.append({'model': model, 'reason': 'duplicate key'})
                continue
            index = {row.get(key): row for row in got}
            pairs = [(row, index.get(row[key], {})) for row in wanted]
        else:
            pairs = list(zip(wanted, got))
        for left, right in pairs:
            for column, value in left.items():
                observed = right.get(column)
                if isinstance(value, Fraction):
                    target = float(value)
                    equal = isinstance(observed, (int, float)) and math.isfinite(observed) and abs(observed - target) <= math.ulp(target)
                else:
                    equal = column in right and observed == value
                if not equal:
                    failures.append({'model': model, 'column': column,
                                     'key': left.get(key) if key else None,
                                     'expected': str(value), 'actual': str(observed)})
    return failures


def snapshot(connection):
    return {model: rows(connection, SCHEMA + '.' + model) for model in MODELS}


def main():
    import duckdb
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    out = args.output.parent / 'phase3-funnel-case'
    out.mkdir(exist_ok=False)
    proof = {'task_key': spec['task_key'], 'case': spec['case'], 'requirements': spec['requirements'],
             'tested_checker_invoked_to_define_validity': False, 'model_requests': 0,
             'validity': 'invalid', 'engine_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    project = Path('/app/dbt_project')
    db = os.environ.get('DUCKDB_PATH', '/app/database/retail.duckdb')
    try:
        with duckdb.connect(db, read_only=True) as connection:
            raw = {name: rows(connection, relation) for name, relation in RAW.items()}
            expected = derive(raw)
            before = snapshot(connection)
        # Persist the independent expected result before any mutation or grader.
        (out / 'independent-expected.json').write_text(json.dumps(expected, default=encoded) + '\n')
        baseline_errors = compare(expected, before)
        assert not baseline_errors, baseline_errors[:8]
        proof.update(raw_digest=digest(raw), baseline_digest=digest(before), baseline_matches_independent=True)
        case = spec['case']
        if case in ('wrong-source-stage', 'wrong-summary', 'wrong-dropoffs'):
            sql = {
                'wrong-source-stage': "UPDATE funnel_analytics.int_session_funnel SET has_purchase=1-has_purchase, funnel_stage='NONE'",
                'wrong-summary': 'UPDATE funnel_analytics.funnel_conversion_rates SET total_sessions=total_sessions+1, view_rate=view_rate+1',
                'wrong-dropoffs': 'UPDATE funnel_analytics.funnel_dropoff_analysis SET session_count=session_count+1, dropoff_rate=dropoff_rate+1',
            }[case]
            with duckdb.connect(db) as connection:
                connection.execute(sql)
            proof['mutation_sql'] = sql
        elif case == 'missing-model':
            path = project / 'models/marts/funnel_conversion_rates.sql'
            proof['removed_source'] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            path.unlink()
            assert not path.exists()
        elif case == 'late-backend':
            # The shared wrapper explicitly suppresses the usual early probe.
            log = Path('/logs/agent/audit-backend-before-submission-shell.json')
            assert not log.exists(), 'Early backend probe exists; late-only claim would be false'
            execution = json.loads(Path('/logs/agent/audit-rebuild/execution.json').read_text())
            assert execution['build_success']
            completed = execution['runs'][-1]['finished_at_utc']
            stamp = datetime.now(timezone.utc).isoformat()
            result = subprocess.run(['bash', '-c', 'echo "$DB_TYPE"'], text=True, capture_output=True)
            assert result.returncode == 0 and result.stdout.strip() == 'duckdb'
            assert datetime.fromisoformat(stamp) > datetime.fromisoformat(completed)
            proof['late_backend'] = {'after_build_utc': completed, 'started_at_utc': stamp,
                                     'command': 'echo "$DB_TYPE"', 'stdout': result.stdout, 'returncode': result.returncode}
        elif case in ('equivalent-reordered', 'nondeterministic'):
            suffix = {'int_session_funnel': 'session_id DESC', 'funnel_conversion_rates': 'total_sessions DESC',
                      'funnel_dropoff_analysis': 'dropoff_stage DESC'}
            paths = [project / 'models/intermediate/int_session_funnel.sql']
            if case == 'equivalent-reordered':
                paths += [project / ('models/marts/' + model + '.sql') for model in MODELS[1:]]
            changed = []
            for path in paths:
                source = path.read_text().strip().rstrip(';')
                replacement = ('SELECT * FROM (' + source + ') phase3_representation ORDER BY ' + suffix[path.stem]) if case == 'equivalent-reordered' else ('SELECT * REPLACE (current_timestamp AS session_start) FROM (' + source + ') phase3_nondeterministic')
                path.write_text(replacement + '\n')
                changed.append({'path': str(path), 'before_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            proof['source_mutations'] = changed
            env = os.environ.copy()
            env.update(DB_TYPE='duckdb', DUCKDB_PATH=db, DBT_PROFILES_DIR=str(project))
            runs, outputs = [], []
            for index in range(2 if case == 'nondeterministic' else 1):
                command = ['dbt', 'run', '--target-path', 'phase3_case_target', '--log-path', 'phase3_case_logs']
                started = datetime.now(timezone.utc).isoformat()
                with (out / f'build-{index}.log').open('w') as log:
                    proc = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
                runs.append({'command': command, 'cwd': str(project), 'returncode': proc.returncode,
                             'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat()})
                assert proc.returncode == 0, 'Case full dbt run failed'
                with duckdb.connect(db, read_only=True) as connection:
                    outputs.append(snapshot(connection))
            proof['case_builds'] = runs
            if case == 'nondeterministic':
                assert outputs[0] != outputs[1]
                proof['repeat_output_digests'] = [digest(x) for x in outputs]
                proof['explicit_repeat_changed'] = True
        else:
            raise ValueError('Unknown explicit case')
        with duckdb.connect(db, read_only=True) as connection:
            after = snapshot(connection)
            after_raw = {name: rows(connection, relation) for name, relation in RAW.items()}
        assert digest(raw) == digest(after_raw), 'Case changed benchmark inputs'
        errors = compare(expected, after)
        proof.update(output_digest=digest(after), raw_unchanged=True, independent_error_count=len(errors),
                     independent_error_examples=errors[:20])
        if case == 'equivalent-reordered':
            assert not errors
            proof['validity'] = 'legal_equivalent'
        else:
            if case not in ('missing-model', 'late-backend'):
                assert errors, 'No demonstrated business error'
            proof['validity'] = 'business_error'
        (out / 'before.json').write_text(json.dumps(before, default=encoded) + '\n')
        (out / 'after.json').write_text(json.dumps(after, default=encoded) + '\n')
        proof['scope'] = 'Explicit single-rule-family output/workflow mutation after blind full dbt construction. Independent input recomputation precedes native grading. Native may rebuild; before/after observations remain authoritative.'
    except Exception as error:
        proof['error'] = {'type': type(error).__name__, 'message': str(error)}
    args.output.write_text(json.dumps(proof, default=encoded, indent=2) + '\n')
    if proof['validity'] == 'invalid':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
