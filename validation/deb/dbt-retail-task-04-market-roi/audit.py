"""Read-only ROI audit of a declared submission snapshot and fixed staging inputs.

This is an experimental task-specific checker. Validation status is in the
task contract; merely running this command does not certify the checker.
Use runtime/.venv/bin/python (locked PyYAML) for the CLI.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE))
from independent import COLUMNS, INTS, KEY, compare_output, recompute
from dbt_bench.io import evidence_ref, file_hash, write_json

MODEL = 'rpt_channel_roi_payback_monthly'
ALLOWED = {'stg_fact_marketing_spend', 'stg_analytics__dim_date', 'stg_analytics__fact_sales',
           'stg_analytics__dim_customer', 'stg_analytics__dim_channel'}
NOT_NULL = {'month_start', 'channel_key', 'impressions', 'clicks', 'conversions', 'spend_amount'}


def exact_counts(rows):
    """Accept equivalent integral numeric representations without rounding IDs.

    JSON decimal literals are parsed as Decimal. Decimal text is supported for
    DuckDB's Decimal export; no binary float conversion is applied to integers.
    """
    normalized = []
    for original in rows:
        row = dict(original)
        for name in INTS:
            value = row.get(name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
                raise ValueError(f'Invalid count representation: {name}')
            value = Decimal(str(value))
            if not value.is_finite() or value != value.to_integral_value():
                raise ValueError(f'Nonintegral or nonfinite count: {name}')
            row[name] = int(value)
        normalized.append(row)
    return normalized


def artifact_checks(sql, yaml_document, manifest):
    """Validate requested artifacts; outputs alone cannot satisfy dbt workflow."""
    issues = []
    if not sql or not isinstance(yaml_document, dict) or yaml_document.get('version') != 2:
        return ['Missing SQL or version-2 model YAML']
    models = [m for m in yaml_document.get('models', []) if m.get('name') == MODEL]
    if len(models) != 1:
        return ['Expected exactly one YAML entry for the requested model']
    model = models[0]

    def tests(item):
        return item.get('tests', []) + item.get('data_tests', [])

    def name(test):
        return test if isinstance(test, str) else next(iter(test), '')

    columns = {c['name']: c for c in model.get('columns', [])}
    for field in sorted(NOT_NULL):
        if field not in columns or 'not_null' not in {name(t) for t in tests(columns[field])}:
            issues.append(f'Missing requested not_null test: {field}')
    unique = []
    for test in tests(model):
        if isinstance(test, dict) and 'dbt_utils.unique_combination_of_columns' in test:
            args = test['dbt_utils.unique_combination_of_columns'] or {}
            args = args.get('arguments', args)
            unique.append(args.get('combination_of_columns', []))
    if not any(len(cols) == len(KEY) and set(cols) == set(KEY) for cols in unique):
        issues.append('Missing requested unique-combination test')

    def descriptions(value):
        if isinstance(value, dict):
            return any(k == 'description' and bool(v) or descriptions(v) for k, v in value.items())
        if isinstance(value, list):
            return any(descriptions(v) for v in value)
        return False

    if descriptions(yaml_document) or re.search(r'{%\s*docs\b|{{\s*doc\s*\(', sql):
        issues.append('Submitted documentation text/block contrary to requested files')
    nodes = manifest.get('nodes', {})
    selected = [(key, node) for key, node in nodes.items()
                if node.get('resource_type') == 'model' and node.get('name') == MODEL]
    if len(selected) != 1:
        return issues + ['Missing or ambiguous built model in manifest']
    _, node = selected[0]
    # dbt strips outer file whitespace when loading raw_code; retain all
    # internal SQL text and compare only this documented representation change.
    if (node.get('raw_code', node.get('raw_sql')) or '').strip() != sql.strip():
        issues.append('Manifest SQL differs from submitted SQL snapshot')
    for dependency in node.get('depends_on', {}).get('nodes', []):
        dep = nodes.get(dependency)
        if dep is None or dep.get('resource_type') != 'model' or dep.get('name') not in ALLOWED:
            issues.append(f'Undeclared input dependency: {dependency}')
    return issues


def evaluate(inputs, output, sql, yaml_document, manifest):
    expected, stats = recompute(inputs)
    try:
        comparison = compare_output(expected, exact_counts(output))
    except (ValueError, TypeError, ArithmeticError) as exc:
        comparison = {'equal': False, 'reason': str(exc)}
    issues = artifact_checks(sql, yaml_document, manifest)
    return {'success': comparison['equal'] and not issues, 'comparison': comparison,
            'artifact_issues': issues, 'input_stats': stats}


def validate_snapshot(directory, snapshot, required):
    """A changed/missing export is different from an attested absent output."""
    if snapshot.get('phase') != 'after_agent_before_native_verifier' or not snapshot.get('read_only_database') or snapshot.get('dbt_invoked'):
        raise ValueError('Invalid collector phase or side effects')
    if snapshot.get('errors'):
        raise ValueError('Collector reported errors')
    absent = []
    for name in required:
        captured = snapshot['files'][name]
        path = directory/name
        if captured.get('absent'):
            if path.exists():
                raise ValueError('Attested absent file exists: '+name)
            absent.append(name)
        elif not path.is_file() or file_hash(path) != captured['sha256']:
            raise ValueError('Snapshot artifact missing or changed: '+name)
    return absent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True,
                        help='Trusted read-only pre-verifier export containing five inputs, output, SQL/YAML and dbt manifest')
    parser.add_argument('--output-name', default='output.json', choices=['output.json', 'after.json'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import yaml
    required = ['marketing.json', 'dates.json', 'sales.json', 'customers.json', 'channels.json',
                args.output_name, 'submission.sql', 'submission.yml', 'dbt-manifest.json']
    paths = [args.snapshot / name for name in required]
    code = [HERE / 'audit.py', HERE / 'independent.py', HERE / 'audit-data.json', ROOT / 'src/dbt_bench/comparison.py']
    version = 'roi-full-input-audit-v1:' + hashlib.sha256(''.join(file_hash(p) for p in code).encode()).hexdigest()
    code_dir = args.output.parent/'checker-source'
    code_dir.mkdir(parents=True, exist_ok=False)
    for path in code:
        shutil.copy2(path, code_dir/path.name)
    result = {'checker_version': version, 'checker_validation': 'consult_task_contract',
              'reward': None, 'success': None, 'status': 'pending',
              'code': [evidence_ref(code_dir/p.name, ROOT) for p in code],
              'code_source_paths': [str(p.relative_to(ROOT)) for p in code],
              'submission': [evidence_ref(p, ROOT) for p in paths if p.is_file()],
              'missing': [p.name for p in paths if not p.is_file()],
              'scope': 'Full declared output and submitted dbt artifacts. No dbt execution; native result and snapshot provenance must be checked separately.'}
    snapshot_path = args.snapshot/'snapshot.json'
    attested_absent = []
    snapshot_error = None
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text())
        try:
            attested_absent = validate_snapshot(args.snapshot, snapshot, required)
        except (ValueError, KeyError) as exc:
            snapshot_error = str(exc)
        result['snapshot'] = evidence_ref(snapshot_path, ROOT)
    result['snapshot_verified'] = snapshot_path.is_file() and snapshot_error is None
    result['snapshot_error'] = snapshot_error
    # Missing capture is unknown. An attested absent required deliverable is
    # a business failure once the fixed inputs and collector are verified.
    if snapshot_error:
        result.update(status='error', error=snapshot_error)
    elif not result['missing'] or result['snapshot_verified'] and set(result['missing']) == set(attested_absent) and set(attested_absent) <= {args.output_name, 'submission.sql', 'submission.yml', 'dbt-manifest.json'}:
        load = lambda name: json.loads((args.snapshot/name).read_text(), parse_float=Decimal)
        try:
            inputs = {name: load(name+'.json') for name in ('marketing', 'dates', 'sales', 'customers', 'channels')}
            pinned = json.loads((HERE/'audit-data.json').read_text())['canonical_input_hashes']
            for name, rows in inputs.items():
                canonical = '\n'.join(sorted(json.dumps(r, sort_keys=True, separators=(',', ':'), default=str) for r in rows))
                if hashlib.sha256(canonical.encode()).hexdigest() != pinned[name]:
                    raise ValueError(f'Fixed initial staging input changed: {name}')
            if attested_absent:
                result.update(success=False, artifact_issues=['Required deliverable absent: '+name for name in attested_absent])
            else:
                result.update(evaluate(inputs,
                                      load(args.output_name), (args.snapshot/'submission.sql').read_text(),
                                      yaml.safe_load((args.snapshot/'submission.yml').read_text()), load('dbt-manifest.json')))
            result.update(status='scored', reward=float(result['success']))
        except (ValueError, TypeError, KeyError, ArithmeticError, yaml.YAMLError) as exc:
            result.update(status='error', error=f'{type(exc).__name__}: {exc}')
    result['submission_sha256'] = file_hash(snapshot_path) if snapshot_path.is_file() else hashlib.sha256(json.dumps(result['submission'], sort_keys=True).encode()).hexdigest()
    write_json(args.output, result)
    print(json.dumps({k: result[k] for k in ('checker_version', 'status', 'reward', 'success', 'missing')}))
    return 0 if result['status'] == 'scored' else 2


if __name__ == '__main__':
    raise SystemExit(main())
