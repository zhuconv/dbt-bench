"""Execute existing finite business checks on six current formal captures."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from dataclasses import replace
import argparse, json, sys, importlib
from dbt_bench import io
from audit_tables import decode
ROOT = Path(__file__).resolve().parents[1]
B = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
D = B / 'phase4-review-v1/operations'
sys.path.insert(0, str(D))
from actual_core_v2 import verify, runtime, ref, save, STAGES, snapshot_tables
sys.path.insert(0, str(ROOT / 'validation/deb/dbt-customer-cohort-retention/phase4_v1'))
from review_support_v3 import outputs
from phase3_capture import Capture
MAP = {'dbt-abc-classification': ('02-recapture-v1', 'expected_v3', 'audit_v4'), 'dbt-gl-reconciliation': ('02-v2', 'expected_v2', 'audit_v2'), 'dbt-fulfillment-sla': ('02-recapture-v1', 'expected_v2', 'audit_v2'), 'dbt-supplier-scorecard': ('03-v3', 'expected_v2', 'audit_v3'), 'dbt-three-way-matching': ('03-v3', 'expected_v1', 'audit_v1'), 'dbt-retail-task-01-channel-revenue': ('03-v3', 'expected_v1', 'audit_v1')}

def capture(v, phase):
    if len(v['hooks'][phase]) != 1:
        raise ValueError('Ambiguous capture')
    path = v['hooks'][phase][0]['directory'] / 'capture.json'
    r = io.read_json(path)
    if not r['database_and_wal_unchanged']:
        raise ValueError('Capture changed source DB')
    raw = {}
    refs = [ref(path)]
    for n, x in r['raw'].items():
        p = runtime(v['trial'], x)
        refs.append(ref(p))
        cols = [c.lower() for c in x['columns']]
        if len(cols) != len(set(cols)):
            raise ValueError('Ambiguous SQL column casing')
        values = [json.loads(l) for l in p.read_text().splitlines()]
        if len(values) != x['rows']:
            raise ValueError('Raw row count differs')
        raw[n] = [dict(zip(cols, map(decode, row))) for row in values]
    return (r, raw, refs)

def run(task, out):
    bn, en, an = MAP[task]
    old = ROOT / 'validation/deb' / task / ('phase4_v1' if task in ('dbt-supplier-scorecard', 'dbt-retail-task-01-channel-revenue', 'dbt-fulfillment-sla', 'dbt-three-way-matching', 'dbt-abc-classification') else 'audit_phase2_v1')
    en, an = ('expected_v1', 'audit_v1') if task in ('dbt-supplier-scorecard', 'dbt-retail-task-01-channel-revenue', 'dbt-fulfillment-sla', 'dbt-three-way-matching', 'dbt-abc-classification') else (en, an)
    en = 'expected_v5' if task == 'dbt-fulfillment-sla' else 'expected_v2' if task == 'dbt-three-way-matching' else en
    sys.path.insert(0, str(old))
    e = importlib.import_module(en)
    a = importlib.import_module(an)
    if out.exists():
        raise ValueError('Immutable probe exists')
    out.mkdir(parents=True)
    batch = B / ('phase4-deb-operations-batch' + bn)
    key = 'deb__' + task
    trials = []
    for rp in (batch / 'jobs').rglob('result.json'):
        cp = rp.with_name('config.json')
        if cp.exists() and task in io.read_json(cp).get('task', {}).get('path', ''):
            trials.append(rp.parent)
    if len(trials) != 2:
        raise ValueError('Need two complete formal trials')
    records = []
    for trial in sorted(trials):
        v = verify(batch, trial)
        ic, raw, refs = capture(v, 'initial')
        fc, after, fr = capture(v, 'final')
        if {k: sorted((json.dumps(r, sort_keys=True, default=str) for r in rs)) for k, rs in raw.items()} != {k: sorted((json.dumps(r, sort_keys=True, default=str) for r in rs)) for k, rs in after.items()}:
            raise ValueError('Initial/final source changed')
        if task.endswith('fulfillment-sla'):
            expected, facts = e.build_variants(raw)
        elif task.endswith('channel-revenue'):
            expected = e.from_captured(raw)
            facts = expected[1]
        else:
            expected = e.derive(raw)
            facts = expected.get('facts', expected.get('profile', {}))
        er = save(out / trial.name / 'independent-expected.json', {'task_key': key, 'expected': expected, 'source_refs': refs, 'code': ref(old / (en + '.py')), 'model_requests': 0})
        z = {'request': {'task_key': key}, 'capture': Capture(trial, ROOT)}
        stages = []
        for stage, part in STAGES:
            sr, o = outputs(z, stage)
            component = {'status': 'pass', 'reason': 'Business-component probe only; actual workflow scored separately, never eligibility from this placeholder'}
            if task.endswith('fulfillment-sla'):
                compiled = {}
                for name, c in fc.get('compiled', {}).items():
                    p = runtime(trial, c)
                    compiled[name] = [dict(zip(c['columns'], map(decode, json.loads(line)))) for line in p.read_text().splitlines()]
                result = a.evaluate('fulfillment', o, expected, workflow=component, compiled=compiled)
            elif task.endswith('channel-revenue'):
                snapshot = io.read_json(ROOT / sr['path'])
                window = {'start': snapshot['started_at_utc'], 'end': snapshot['finished_at_utc']} if 'started_at_utc' in snapshot else None
                result = a.evaluate(o, expected, workflow=component, timestamp_window=window)
            else:
                result = a.evaluate(o, expected, component)
            result.update(task_key=key, candidate_snapshot=sr, independent_expected=er)
            ar = save(out / trial.name / (stage + '-component.json'), result)
            stages.append({'stage': stage, 'audit': ar, 'reward': result.get('reward'), 'snapshot': sr, 'tables': snapshot_tables(ROOT / sr['path'])})
        records.append({'trial': trial.name, 'native_result': ref(trial / 'result.json'), 'execution': ref(trial / 'agent/audit-rebuild/execution.json'), 'stages': stages, 'facts': facts, 'raw': refs + fr, 'actual_commands': [r['command'] for r in v['execution']['runs']], 'initial_capture_error': ic.get('capture_error'), 'final_capture_error': fc.get('capture_error'), 'label': v['label']})
    summary = {'task_key': key, 'records': records, 'six_stage_component_rewards': [s['reward'] for r in records for s in r['stages']], 'model_requests': 0, 'batch': ref(batch / 'manifest.json'), 'scope': 'Executed read-only independent current-data components. Pending/failed business policies are retained, not upgraded by native1; workflow placeholder is explicitly not candidate certification.'}
    save(out / 'summary.json', summary)
    print({k: summary[k] for k in ('task_key', 'six_stage_component_rewards')})
if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--task', required=True)
    p.add_argument('--output', type=Path, required=True)
    x = p.parse_args()
    run(x.task, x.output)
