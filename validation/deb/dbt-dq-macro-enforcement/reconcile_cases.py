"""Recompute DQ proofs from retained inputs; keep native errors and null rewards."""
import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref,file_hash,read_json,tree_hash,write_json
from independent import encoded,derive,compare


def reconcile(batch):
    batch=batch.resolve()
    manifest=read_json(batch/'manifest.json');planned={p['task_label']:p for p in manifest['planned']}
    seen=set();trials=[];reference_outputs=[]
    for path in sorted((batch/'jobs').glob('*/*/result.json')):
        result=read_json(path);label=Path(result['config']['task']['path']).name
        if label not in planned or label in seen:raise ValueError('Unknown or duplicate case: '+label)
        seen.add(label);entry=manifest['tasks'][label]
        assert tree_hash(batch/'tasks'/label)==entry['validation_copy_hash']
        case_path=ROOT/entry['case']['path'];assert file_hash(case_path)==entry['case']['sha256']
        case=read_json(case_path);proof_path=path.parent/'agent/case-proof.json'
        proof=read_json(proof_path) if proof_path.exists() else None
        snapshots={};valid=False
        if proof:
            assert proof['case']==case['mode'] and proof['tested_checker_invoked_to_define_validity'] is False
            inputs_path=path.parent/'agent/dq-inputs.json';assert file_hash(inputs_path)==case['pristine_inputs_sha256']
            expected=derive(read_json(inputs_path))
            for when in ['before','after']:
                snapshot_path=path.parent/f'agent/dq-{when}/snapshot.json'
                record=next(r for r in proof['submission_artifacts'] if r['path'].endswith(f'/dq-{when}/snapshot.json'))
                assert file_hash(snapshot_path)==record['sha256']
                snapshot=read_json(snapshot_path)
                for ref in snapshot['files']:
                    relative=ref['path'].split(f'/dq-{when}/',1)[1]
                    assert file_hash(snapshot_path.parent/relative)==ref['sha256']
                comparison=compare(expected,snapshot)
                assert encoded(comparison)==encoded(proof['facts']['baseline_comparison' if when=='before' else 'comparison'])
                if when=='before':assert comparison['equal']
                if when=='after' and case['mode']=='reference':reference_outputs.append(sorted(encoded(r) for r in snapshot['rows']))
                snapshots[when]=evidence_ref(snapshot_path,ROOT)
            model=path.parent/'agent/dq-after/models/marts/sales/fct_order_line_detail.sql'
            actual_model=next(r for r in proof['submission_artifacts'] if r['path'].startswith('/app/'))
            assert file_hash(model)==actual_model['sha256']
            valid=result.get('exception_info') is None
        rewards=(result.get('verifier_result') or {}).get('rewards')
        log_path=path.parent/'verifier/test-stdout.txt';log=log_path.read_text() if log_path.exists() else ''
        outcome='unresolved_execution'
        if valid and rewards=={'reward':1.0}:
            outcome='missed_wrong' if case['kind']=='negative' else 'accepted_scoped_positive'
        elif valid and rewards=={'reward':0.0}:
            if 'ERROR at setup' not in log and 'ERROR collecting' not in log and 'E   duckdb.' not in log and 'AssertionError:' in log:
                outcome='detected_wrong' if case['kind']=='negative' else 'rejected_scoped_positive'
        trials.append({**planned[label],'trial_id':result['id'],'kind':case['kind'],'requirement_ids':case['requirement_ids'],
            'case_outcome':outcome,'native_rewards':rewards,'exception_info':result.get('exception_info'),
            'raw_directory':str(path.parent.relative_to(ROOT)),
            'proof_validity':proof['validity'] if proof else None,
            'independent_proof':evidence_ref(proof_path,ROOT) if proof else None,
            'snapshots':snapshots,'result':evidence_ref(path,ROOT),
            'native_log':evidence_ref(log_path,ROOT) if log_path.exists() else None})
    report={'schema_version':1,'planned_trials':len(planned),'observed_trials':len(trials),
        'missing':sorted(planned.keys()-seen),'coverage_complete':set(planned)==seen,
        'outcomes':dict(Counter(t['case_outcome'] for t in trials)),
        'reference_output_stable':len(reference_outputs)==2 and reference_outputs[0]==reference_outputs[1],
        'reference_output_scope':'All output columns/rows as a multiset; no unstable columns dropped',
        'trials':trials,'full_task_validation_complete':False,
        'scope':'Scoped fixed-input pricing/normalization and structural cases; other DQ semantics and required negative coverage remain pending.'}
    output=batch/'reconciliation.json';assert not output.exists();write_json(output,report)
    print({k:report[k] for k in ['planned_trials','observed_trials','missing','outcomes','reference_output_stable']})
    return 0 if report['coverage_complete'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,required=True)
    raise SystemExit(reconcile(parser.parse_args().batch))
