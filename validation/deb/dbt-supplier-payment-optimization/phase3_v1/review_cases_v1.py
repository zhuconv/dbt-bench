"""Execute retained real negative submissions only after matching current raw inputs."""
from pathlib import Path
from collections import Counter
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / 'scripts'), str(ROOT / 'src')]
from dbt_bench import io
from run_audit_v4 import run, dec


def checked(ref):
    if not io.evidence_ok(ref, ROOT):
        raise ValueError('Retained evidence changed')
    return ROOT / ref['path']


def save(path, value):
    if path.exists():
        raise ValueError('Do not replace case evidence')
    io.write_json(path, json.loads(json.dumps(value, default=str, allow_nan=False)))
    return io.evidence_ref(path, ROOT)


def bind_inputs(previous_ref, current_ref):
    old, new = [dec(io.read_json(checked(r))) for r in (previous_ref, current_ref)]
    for r in old['source_refs'] + new['source_refs']:
        checked(r)
    new_rows = {k.lower(): v for k, v in new['rows'].items()}
    proofs = {}
    for name, rows in old['rows'].items():
        columns = sorted(rows[0])
        def bag(data):
            return Counter(tuple((type(r[k]).__name__, r[k]) for k in columns) for r in data)
        proofs[name] = bag(rows) == bag(new_rows[name.lower()])
    if not all(proofs.values()):
        raise ValueError('Retained raw projection differs: ' + str(proofs))
    return proofs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--fresh', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    prior = io.read_json(a.previous)
    fresh = io.read_json(a.fresh)
    current = fresh['records'][0]['original_input']
    records = []
    for case in prior['records']:
        proof_path = checked(case['independent_proof'])
        original_spec = checked(case['spec'])
        spec = io.read_json(original_spec)
        binding = bind_inputs(spec['original_input'], current)
        d = a.output / case['rule_id']
        d.mkdir()
        proof = save(d / 'input-binding-before-audit.json', {'rule_id': case['rule_id'],
            'current_raw_projection_exact': binding, 'current_input': current,
            'retained_original_input': spec['original_input'], 'retained_independent_proof': case['independent_proof'],
            'independent_proof_read_before_audit': True,
            'scope': 'Re-execute retained actually constructed submission; same typed original input multiplicity, no current oracle invocation defines wrongness.'})
        spec['original_input'] = current
        spec_ref = save(d / 'request.json', spec)
        result = run(spec, ROOT)
        audit = save(d / 'audit.json', result)
        target = result.get('checks', {}).get(case['target_check'], {})
        records.append({'rule_id': case['rule_id'], 'target': case['target_check'],
            'target_status': target.get('status'), 'reward': result['reward'],
            'independent_proof': case['independent_proof'], 'input_binding': proof,
            'request': spec_ref, 'audit': audit, 'prior_audit': case['audit'],
            'native_reward_retained': case['native_reward'],
            'detected_target': target.get('status') == 'fail', 'all_failed_checks': [k for k, c in result.get('checks', {}).items() if c['status'] == 'fail']})
        print(case['rule_id'], target.get('status'), result['reward'], flush=True)
    print(save(a.output / 'review.json', {'task_key': prior['task_key'], 'records': records,
        'target_count': len(records), 'target_detected': sum(r['detected_target'] for r in records),
        'prior_case_review': io.evidence_ref(a.previous, ROOT), 'fresh_review': io.evidence_ref(a.fresh, ROOT),
        'code': io.evidence_ref(Path(__file__), ROOT), 'model_requests': 0, 'native_rewards_modified': False}))


if __name__ == '__main__':
    main()
