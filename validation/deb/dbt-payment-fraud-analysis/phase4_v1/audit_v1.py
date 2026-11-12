"""Finite original-input audit plus the phase4 scored-order fraction rule.

Remaining pending-status and fractional-representation ambiguity deliberately
prevents certification even when all finite checks accept the submission.
"""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from collections import Counter
from decimal import Decimal
import argparse
import importlib.util
import json
import sys
import yaml
HERE = Path(__file__).resolve().parent
LEGACY = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase2-review-v1/deb_commerce/payment-stockout-v1'))
sys.path.insert(0, str(LEGACY))
from common_io_v1 import read_hashed, load_raw, encode
from audit_suite_v4 import inspect, load_capture, CONFIG
from metadata_check_v1 import missing_columns
from audit_tables import CaptureError
from derive_v2 import derive
ps = importlib.util.spec_from_file_location('selected_profile', HERE.parents[1] / 'dbt-customer-risk-scoring/phase4_v1/profile_runtime_v2.py')
profile = importlib.util.module_from_spec(ps)
ps.loader.exec_module(profile)
NAME = 'dbt-payment-fraud-analysis'
KEY = 'deb__' + NAME

def scored_fraction(rows, raw):
    source = [{k.lower(): v for k, v in row.items()} for row in raw['fraud']]
    unique = {r['order_id'] for r in source}
    if len(unique) != len(source) or not source:
        return {'reward': None, 'reason': 'Scored source order grain unavailable or nonunique'}
    counts = Counter((r['risk_level'] for r in source))
    expected = {k: Decimal(n) / len(source) for k, n in counts.items()}
    errors = []
    seen = Counter((r.get('risk_level') for r in rows))
    if seen != Counter({k: 1 for k in expected}):
        errors.append({'rule': 'risk_grain', 'seen': dict(seen)})
    for row in rows:
        want = expected.get(row.get('risk_level'))
        actual = row.get('pct_of_total_orders')
        equal = type(actual) is float and actual == float(want) if want is not None else False
        if want is not None and type(actual) in (int, Decimal):
            equal = Decimal(actual) == want
        if not equal:
            errors.append({'rule': 'scored_order_fraction', 'risk_level': row.get('risk_level'), 'actual': actual, 'expected': want})
    return {'reward': 0.0 if errors else 1.0, 'expected': expected, 'errors': errors, 'denominator': len(source), 'reason': 'Original scored-order counts, exact keys and count ratios; no percentage scaling or unscored-order denominator'}

def workflow(facts):
    try:
        if facts.get('stage') == 'initial':
            return {'reward': 0.0 if facts.get('required_models_absent') else None, 'reason': 'Actual initial inventory; no construction inferred'}
        files = facts['files']
        process = facts['process']
        env = process['environment']
        cfg = yaml.safe_load(files['dbt_project.yml'])
        prof = yaml.safe_load(files['profiles.yml'])['dbt_project']
        name = profile.scalar(prof['target'], env)
        target = {k: profile.scalar(v, env) for k, v in prof['outputs'][name].items()}
        checks = {'profile_name': cfg['profile'] == 'dbt_project', 'type': target['type'] == 'duckdb', 'database': target['path'] == '/app/database/retail.duckdb', 'profile_location': env['DBT_PROFILES_DIR'] == '/app/dbt_project', 'project_location': process['cwd'] == '/app/dbt_project', 'build': process['returncode'] == 0 and facts['fresh_invocation_bound'], 'backend_before_install': facts['backend_before_install'], 'required_source_graph': facts['source_graph_complete']}
        return {'reward': 1.0 if all(checks.values()) else 0.0, 'checks': checks, 'reason': 'Actual source/config/manifest/process/echo chronology; no SQL hash whitelist'}
    except profile.UnresolvedProfile as exc:
        return {'reward': None, 'reason': str(exc)}
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        return {'reward': 0.0, 'reason': 'Invalid captured project/profile: ' + str(exc)}

def run(spec, root):
    raw, _, rawmeta = load_raw(spec['raw_snapshot'], root, KEY, '/app/database/retail.duckdb')
    _, execution = read_hashed(spec['execution'], root)
    if execution['task_key'] != KEY or execution['model_requests'] != 0 or execution['initial_input_hashes'] != [{'path': '/app/database/retail.duckdb', 'sha256': rawmeta['input_sha256']}]:
        raise CaptureError('Original source bytes do not match actual initial database')
    _, snap = read_hashed(spec['candidate_snapshot'], root)
    if snap['task_key'] != KEY or snap['stage'] != spec['candidate_stage']:
        raise CaptureError('Task/stage mismatch')
    companions = {}
    tables = {}
    for rel in CONFIG[NAME]['relations']:
        try:
            companions[rel] = load_capture(spec, root, NAME, rel)
        except (CaptureError, ValueError, OSError, KeyError, TypeError):
            pass
    for rel in CONFIG[NAME]['relations']:
        if rel in companions:
            tables[rel] = inspect(companions[rel], rel, raw, NAME, companions)
        else:
            tables[rel] = missing_columns(spec['candidate_snapshot'], root, KEY, '/app/database/retail.duckdb', rel, spec['candidate_stage'], CONFIG[NAME]['relations'][rel]) or {'reward': None, 'reason': 'Required declared relation unavailable'}
    rel = 'fraud_analytics.fraud_risk_summary'
    fraction = scored_fraction(companions[rel].rows, raw) if rel in companions else {'reward': None, 'reason': 'Fraction output absent'}
    _, wf = read_hashed(spec['workflow'], root)
    w = workflow(wf)
    scores = [v['reward'] for v in tables.values()] + [fraction['reward'], w['reward']]
    reward = 0.0 if 0 in scores else None
    return {'task_key': KEY, 'reward': reward, 'candidate_snapshot': spec['candidate_snapshot'], 'tables': tables, 'scored_fraction': fraction, 'workflow': w, 'original_input': spec['raw_snapshot'], 'scope': 'Finite independent assertions; AUTHORIZED/pending classification and unspecified precision remain unresolved, so no whole-task positive certificate', 'checker_version': 'phase4-payment-scored-fraction-v1', 'model_requests': 0}
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--root', default='.')
    a = p.parse_args()
    result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(result, default=encode, indent=2) + '\n')
    print(json.dumps({'reward': result['reward']}))
