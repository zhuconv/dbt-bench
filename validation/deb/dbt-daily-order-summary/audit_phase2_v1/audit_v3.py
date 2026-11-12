"""Supplementary daily audit. Inputs are trusted runner captures, never agent claims.

Callers hash-validate artifact refs and supply CapturedTable plus initial expected
rows. A table pass cannot certify missing workflow evidence. No native imports.
"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
import yaml

from audit_tables import TablePolicy, audit_table
from dbt_bench.comparison import Column, TableContract, compare_tables

NUMERIC_TYPES = ('TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT',
                 'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT', 'FLOAT', 'DOUBLE',
                 'REAL') + tuple(f'DECIMAL({p},{s})' for p in range(1, 39) for s in range(p + 1))
TABLE = TableContract((Column('order_date', 'date'), Column('order_count', 'numeric'),
                       Column('total_revenue', 'numeric')), keys=('order_date',), allow_extra_columns=True)
POLICY = TablePolicy(TABLE, {'order_date': ('DATE',), 'order_count': NUMERIC_TYPES,
                            'total_revenue': NUMERIC_TYPES},
                     not_null=('order_date', 'order_count', 'total_revenue'),
                     relation_types=('BASE TABLE', 'TABLE'))


def check(status, reason, **details):
    return dict(status=status, reason=reason, **details)


def audit_values(candidate, expected):
    result = audit_table(candidate, POLICY, expected)
    if result['status'] != 'scored':
        return result
    count_ok, revenue_ok = True, True
    for row in candidate.rows:
        for field in ('order_count', 'total_revenue'):
            value = row.get(field)
            valid = type(value) in (int, float, Decimal)
            number = Decimal(str(value)) if valid else Decimal('NaN')
            valid = valid and number.is_finite()
            if field == 'order_count':
                count_ok &= valid and number >= 0 and number == number.to_integral_value()
            else:
                parts = number.as_tuple()
                revenue_ok &= valid and (parts.exponent >= -2 or not any(parts.digits[-min(len(parts.digits), -2-parts.exponent):]))
    result['checks'].update(nonnegative_integral_count=bool(count_ok), finite_cent_revenue=bool(revenue_ok))
    result['reward'] = float(all(result['checks'].values()))
    return result


def _project_schema(value):
    if not isinstance(value, dict):
        return False
    return any(k in ('schema', '+schema') or _project_schema(v) for k, v in value.items())


def audit_workflow(capture=None):
    """Use only harness-validated capture, with missing fields explicitly pending.

    files are text content whose hash was validated by the caller; probed_paths
    distinguish actual absent files from uncaptured paths. Process/events are
    accepted only via the caller's separately verified trusted runner boundary.
    Source/compiled SQL hashes bind artifacts but never restrict legal SQL.
    """
    c = capture or {}
    out = {name: check('pending', 'Required trusted execution capture not supplied') for name in
           ('backend_before_code', 'fresh_project', 'project_model', 'profile_location',
            'profile_configuration', 'dbt_process', 'model_build')}
    files, probed = c.get('files', {}), set(c.get('probed_paths', []))
    project = files.get('dbt_project.yml')
    manifest = c.get('manifest')
    if 'dbt_project.yml' in probed and project is None:
        out['project_model'] = check('fail', 'Actually probed /app/dbt_project/dbt_project.yml is absent')
    if 'profiles.yml' in probed:
        out['profile_location'] = check('pass' if 'profiles.yml' in files else 'fail',
                                        'Actual project-local profile existence capture')
    if project is not None and 'profiles.yml' in files:
        try:
            cfg, profiles = yaml.safe_load(project), yaml.safe_load(files['profiles.yml'])
            profile = profiles['dbt_project']; target = profile['outputs'][profile['target']]
            path_ok = target.get('path') == c.get('database')
            # Unrendered env_var may be legal; only actual resolved process/adapter
            # capture can settle it. Never fail an expression for spelling alone.
            templated = isinstance(target.get('path'), str) and '{{' in target['path']
            basic = cfg.get('profile') == 'dbt_project' and target.get('type') == 'duckdb' and target.get('schema') == 'daily_analytics' and not _project_schema(cfg.get('models', {})) and 'schema' not in cfg
            status = 'pass' if basic and path_ok else 'pending' if '{{' in project or '{{' in files['profiles.yml'] else 'fail'
            out['profile_configuration'] = check(status, 'Parsed declared project/profile names, schema placement, backend and database path; templated paths need resolved runtime capture')
        except (KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
            out['profile_configuration'] = check('pending' if '{{' in project or '{{' in files['profiles.yml'] else 'fail', 'Captured profile requires runtime template resolution or has invalid configuration: ' + str(exc))
    if isinstance(manifest, dict):
        nodes = [(k, n) for k, n in manifest.get('nodes', {}).items()
                 if n.get('resource_type') == 'model' and n.get('name') == 'daily_order_summary']
        if len(nodes) == 1:
            uid, node = nodes[0]; path = node.get('original_file_path')
            if path in probed:
                valid = path in files and not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts
                out['project_model'] = check('pass' if valid else 'fail', 'Manifest-named model file was actually probed in the standalone project', model_path=path)
            run = c.get('run_results', {})
            results = [r for r in run.get('results', []) if r.get('unique_id') == uid]
            if c.get('trusted_process_complete') and c.get('process') and len(results) == 1:
                process = c['process']; env = process.get('environment', {})
                process_ok = process.get('exit_code') == 0 and process.get('cwd') == '/app/dbt_project' and env.get('DBT_PROFILES_DIR') == '/app/dbt_project' and env.get('DB_TYPE') == 'duckdb' and env.get('DUCKDB_PATH', '/app/database/retail.duckdb') == c.get('database') and process.get('dbt_command_executed') is True
                out['dbt_process'] = check('pass' if process_ok else 'fail', 'Trusted actual dbt process cwd/environment/exit and invocation capture')
                build_ok = process_ok and results[0].get('status') == 'success' and node.get('schema') == 'daily_analytics' and node.get('alias') == 'daily_order_summary' and node.get('config', {}).get('materialized') == 'table' and process.get('artifacts_bound_to_process') is True
                out['model_build'] = check('pass' if build_ok else 'fail', 'Successful actual run result and manifest are bound to delivered model/relation and trusted process')
    if c.get('trusted_initial_capture_complete') and 'initial_project_exists' in c:
        out['fresh_project'] = check('pass' if c['initial_project_exists'] is False else 'fail' if c.get('initial_project_contains_dbt_files') is True else 'pending', 'Actual initial standalone-project existence capture; an existing empty directory alone is not a workflow violation')
    if c.get('trusted_event_history_complete'):
        events = c.get('events', []); echoes = [e for e in events if e.get('kind') == 'live_backend_echo' and e.get('value') == 'duckdb' and e.get('exit_code') == 0]; actions = [e for e in events if e.get('kind') in ('candidate_code_write', 'candidate_subagent_spawn')]
        if actions:
            ok = bool(echoes) and min(e['monotonic_ns'] for e in echoes) < min(e['monotonic_ns'] for e in actions)
            out['backend_before_code'] = check('pass' if ok else 'fail', 'Trusted live shell echo and first candidate code-write/spawn chronology; context probe is not substituted for requested echo')
    if c.get('trusted_backend_before_install'):
        evidence = c['trusted_backend_before_install']
        echo = evidence['shell']
        finished = datetime.fromisoformat(echo['finished_at_utc'].replace('Z', '+00:00'))
        bound = datetime.fromisoformat(evidence['source_install_after_utc'].replace('Z', '+00:00'))
        ok = echo.get('exit_code') == 0 and echo.get('command') == 'echo "$DB_TYPE"' and echo.get('output') in ('duckdb', 'duckdb\n', 'duckdb\\n') and finished < bound and evidence.get('initial_project_exists') is False and evidence.get('controlled_sequential_runner_verified') is True
        out['backend_before_code'] = check('pass' if ok else 'fail', 'Actual shell echo finished before the verified controlled runner source-install lower bound; no fabricated code-write timestamp. Scope is this new clean reconstruction, not prior candidate history.')
    return out


def evaluate(candidate, expected, *, workflow=None, ordered_result=None, repeated=None, table_capture_error=None):
    data = audit_values(candidate, expected) if candidate is not None else dict(status='unknown', reward=None, checks={}, reason=table_capture_error or 'Required table capture is unavailable')
    checks = audit_workflow(workflow)
    checks['table_values'] = check('pending' if data['status'] != 'scored' else 'pass' if data['reward'] == 1 else 'fail', data['reason'], details=data)
    checks['ordered_result'] = check('pending', 'Actual compiled SELECT result order was not captured; sorted/table observer output cannot substitute')
    if ordered_result is not None:
        ordered_contract = TableContract(TABLE.columns, keys=TABLE.keys, ordered=True, allow_extra_columns=True)
        result = compare_tables(expected, ordered_result, ordered_contract)
        checks['ordered_result'] = check('pass' if result.equal else 'fail', result.reason)
    checks['idempotency'] = check('pending', 'Repeated actual dbt execution and its relation capture were not supplied')
    if repeated is not None:
        second = audit_values(repeated, expected)
        checks['idempotency'] = check('pending' if second['status'] != 'scored' else 'pass' if second['reward'] == 1 and data.get('reward') == 1 else 'fail', 'Actual post-repeat capture independently compared to original expectation', details=second)
    states = [v['status'] for v in checks.values()]
    reward = 0.0 if 'fail' in states else None if 'pending' in states else 1.0
    return {'status': 'scored' if reward is not None else 'unknown', 'reward': reward, 'checks': checks,
            'scope': 'Supplementary audit only. Missing execution evidence remains pending. Native rewards are never changed.'}
