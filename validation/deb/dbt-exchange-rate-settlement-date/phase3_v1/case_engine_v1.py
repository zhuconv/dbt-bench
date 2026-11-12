"""Persistent submission-source cases after the author's full project build.

The extra selected build isolates the deliberate target mutation; it does not
replace or narrow the preceding, unchanged full author build.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from expected_v2 import derive, KEYS

PROJECT = Path('/app/dbt_models_duckdb')
MODEL = 'models/marts/finance/fact_revenue.sql'
RAW = {'sales': 'main.fct_sales', 'payments': 'main.stg_orders__order_payments',
       'rates': 'main.dim_exchange_rates'}

def encode(value):
    if isinstance(value, (datetime, date, Decimal, Fraction)):
        return {'$type': type(value).__name__.lower(), 'value': str(value)}
    raise TypeError(type(value).__name__)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=encode).encode()).hexdigest()

def read_rows(connection, relation):
    result = connection.execute('SELECT * FROM ' + relation)
    columns = [column[0].lower() for column in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]

def differences(expected, actual):
    """Independent exact keyed numeric proof; no tested checker invoked."""
    key = KEYS['fact_revenue']
    wanted = {tuple(row[k] for k in key): row for row in expected['rows']['fact_revenue']}
    got = {tuple(row.get(k) for k in key): row for row in actual}
    errors = []
    if len(got) != len(actual): errors.append({'reason': 'duplicate four-key groups'})
    if set(got) != set(wanted):
        errors.append({'reason': 'source four-key population mismatch',
                       'missing': len(wanted.keys() - got.keys()), 'extra': len(got.keys() - wanted.keys())})
    for key in wanted.keys() & got.keys():
        left, right = wanted[key], got[key]
        for column, value in left.items():
            if column.startswith('_'): continue
            actual_value = right.get(column)
            if isinstance(value, (Fraction, Decimal)):
                if isinstance(actual_value, float) and math.isfinite(actual_value):
                    terms = 3 if column == 'discount_rate' else 1
                    equal = abs(Fraction.from_float(actual_value) - Fraction(value)) <= Fraction.from_float(math.ulp(actual_value)) * terms
                else:
                    equal = type(actual_value) in (int, Decimal) and Fraction(actual_value) == Fraction(value)
            else:
                equal = type(actual_value) is type(value) and actual_value == value
            if not equal:
                errors.append({'key': str(key), 'column': column, 'expected': str(value), 'actual': str(actual_value)})
    return errors

def mutate_source(source, case):
    sql = source.strip().rstrip(';')
    if case == 'shift-dates':
        return 'SELECT * REPLACE (CAST(settlement_date + INTERVAL 1 DAY AS DATE) AS settlement_date) FROM (' + sql + ') phase3_case\n'
    if case == 'wrong-conversion':
        return 'SELECT * REPLACE (CAST(2 AS DECIMAL(18,6)) AS fx_rate, net_revenue_usd+11 AS net_revenue_usd, total_revenue_usd+13 AS total_revenue_usd) FROM (' + sql + ') phase3_case\n'
    if case == 'null-rate':
        return 'SELECT * REPLACE (CAST(NULL AS DECIMAL(18,6)) AS fx_rate) FROM (' + sql + ') phase3_case\n'
    if case == 'population':
        return 'WITH phase3_case AS (' + sql + ') SELECT * FROM phase3_case WHERE currency_code IS NOT NULL AND source_system IS NOT NULL UNION ALL SELECT * FROM phase3_case WHERE currency_code IS NOT NULL AND source_system IS NOT NULL\n'
    if case == 'interface':
        return "{{ config(materialized='view') }}\nSELECT * EXCLUDE (avg_unit_price) REPLACE (CAST(settlement_date AS VARCHAR) AS settlement_date, CAST(fx_rate AS DOUBLE) AS fx_rate, CAST(net_revenue_usd AS DOUBLE) AS net_revenue_usd, CAST(total_revenue_usd AS DOUBLE) AS total_revenue_usd) FROM (" + sql.replace("materialized='table'", "materialized='view'") + ') phase3_case\n'
    if case == 'equivalent-reordered':
        return 'SELECT * FROM (' + sql + ') phase3_case ORDER BY order_date DESC, settlement_date DESC, currency_code DESC NULLS FIRST, source_system DESC NULLS FIRST\n'
    raise ValueError('Unsupported source mutation: ' + case)

def main():
    import duckdb
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    assert spec['task_key'] == 'deb__dbt-exchange-rate-settlement-date'
    out = args.output.parent / 'phase3-settlement-case'
    out.mkdir(exist_ok=False)
    proof = {'task_key': spec['task_key'], 'case': spec['case'], 'requirements': spec['requirements'],
             'tested_checker_invoked_to_define_validity': False, 'validity': 'invalid', 'model_requests': 0,
             'engine_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    db = os.environ.get('DUCKDB_PATH', '/app/database/retail.duckdb')
    try:
        execution = json.loads(Path('/logs/agent/audit-rebuild/execution.json').read_text())
        assert execution['build_success'] and len(execution['runs']) == 1
        command = execution['runs'][0]['command']
        assert command[:2] == ['dbt', 'run'] and not any(x in command for x in ['-s', '--select', '--exclude'])
        with duckdb.connect(db, read_only=True) as connection:
            raw = {k: read_rows(connection, v) for k, v in RAW.items()}
            before = read_rows(connection, 'main.fact_revenue')
        expected = derive(raw)
        errors = differences(expected, before)
        assert not errors, errors[:5]
        (out/'independent-expected.json').write_text(json.dumps(expected, default=encode)+'\n')
        (out/'raw-inputs.json').write_text(json.dumps(raw, default=encode)+'\n')
        (out/'before.json').write_text(json.dumps(before, default=encode)+'\n')
        proof.update(raw_digest=digest(raw), baseline_matches_independent=True, baseline_digest=digest(before))
        case = spec['case']; path = PROJECT/MODEL; source = path.read_text()
        (out/'source-before.sql').write_text(source)
        if case in ('shift-dates','wrong-conversion','null-rate','population','interface','equivalent-reordered'):
            path.write_text(mutate_source(source,case))
            (out/'source-after.sql').write_bytes(path.read_bytes())
            command = ['dbt','run','--select','fact_revenue','--target-path','phase3_case_target','--log-path','phase3_case_logs']
            started = datetime.now(timezone.utc).isoformat()
            with (out/'case-build.log').open('w') as log:
                process = subprocess.run(command,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            proof['case_builds']=[{'command':command,'cwd':str(PROJECT),'started_at_utc':started,
                                  'finished_at_utc':datetime.now(timezone.utc).isoformat(),'returncode':process.returncode}]
            assert process.returncode == 0, 'Selected counterfactual build failed'
            for artifact in ('manifest.json','run_results.json'):
                (out/artifact).write_bytes((PROJECT/'phase3_case_target'/artifact).read_bytes())
            proof['source_mutation']={'path':str(path),'before_sha256':hashlib.sha256(source.encode()).hexdigest(),
                                      'after_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        elif case == 'missing-model':
            path.unlink(); assert not path.exists()
            proof['removed_source']={'path':str(path),'sha256':hashlib.sha256(source.encode()).hexdigest()}
        elif case == 'late-backend':
            assert not Path('/logs/agent/audit-backend-before-submission-shell.json').exists()
            started=datetime.now(timezone.utc).isoformat()
            proc=subprocess.run(['bash','-c','echo "$DB_TYPE"'],capture_output=True,text=True)
            assert proc.returncode == 0 and proc.stdout.strip()=='duckdb'
            assert datetime.fromisoformat(started)>datetime.fromisoformat(execution['runs'][0]['finished_at_utc'])
            proof['late_backend']={'started_at_utc':started,'after_build_utc':execution['runs'][0]['finished_at_utc'],
                                   'command':'echo "$DB_TYPE"','stdout':proc.stdout,'returncode':proc.returncode}
        elif case == 'bad-profile':
            profile=PROJECT/'profiles.yml';(out/'profile-before.yml').write_bytes(profile.read_bytes())
            profile.write_text('retail_dw_master:\n  target: phase3_wrong\n  outputs:\n    phase3_wrong:\n      type: duckdb\n      path: /tmp/phase3-wrong-database.duckdb\n      threads: 1\n')
            (out/'profile-after.yml').write_bytes(profile.read_bytes())
            proof['wrong_database_path']='/tmp/phase3-wrong-database.duckdb'
        elif case == 'upstream-source':
            manifest=json.loads((PROJECT/'phase3_target_v2/manifest.json').read_text())
            node=next(n for n in manifest['nodes'].values()if n.get('name')=='stg_orders__order_payments'and n.get('resource_type')=='model')
            upstream=PROJECT/node['original_file_path'];original=upstream.read_bytes();(out/'upstream-before.sql').write_bytes(original)
            upstream.write_bytes(original+b'\n-- phase3 deliberately forbidden upstream modification\n');(out/'upstream-after.sql').write_bytes(upstream.read_bytes())
            proof['upstream_source_mutation']={'path':str(upstream),'before_sha256':hashlib.sha256(original).hexdigest(),'after_sha256':hashlib.sha256(upstream.read_bytes()).hexdigest(),'scope':'Actual forbidden source modification; values unaffected, not a fabricated business numeric error.'}
        else: raise ValueError(case)
        with duckdb.connect(db,read_only=True)as connection:
            after=read_rows(connection,'main.fact_revenue');after_raw={k:read_rows(connection,v)for k,v in RAW.items()}
            proof['physical_schema']=connection.execute('DESCRIBE main.fact_revenue').fetchall()
            proof['relation_type']=connection.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='main' AND table_name='fact_revenue'").fetchone()[0]
        assert digest(raw)==digest(after_raw), 'Counterfactual altered input relations'
        errors=differences(expected,after);proof.update(raw_unchanged=True,independent_error_count=len(errors),independent_error_examples=errors[:20])
        if case=='equivalent-reordered':
            assert not errors;proof['validity']='legal_equivalent'
        else:
            if case not in ('missing-model','late-backend','bad-profile','upstream-source'): assert errors
            proof['validity']='business_error'
        (out/'after.json').write_text(json.dumps(after,default=encode)+'\n')
        proof['scope']='Full author build first. Explicit post-build source/workflow mutation; semantic cases separately build only the changed model to preserve the actual rate dimension. Native target rebuild sees the persistent mutation. All values and timestamps remain captured.'
    except Exception as error:
        proof['error']={'type':type(error).__name__,'message':str(error)}
    args.output.write_text(json.dumps(proof,default=encode,indent=2)+'\n')
    if proof['validity']=='invalid': raise SystemExit(1)

if __name__=='__main__': main()
