"""Inspect frozen case payloads and retain raw native outcomes for every trial."""
from collections import Counter
from pathlib import Path
import argparse,sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import read_json,write_json,evidence_ref,file_hash,tree_hash
from independent import derive,compare


def reconcile(batch):
    manifest=read_json(batch/'manifest.json')
    planned={t['task_label']:t for t in manifest['planned']}
    seen=set();trials=[]
    for path in sorted((batch/'jobs').glob('*/*/result.json')):
        result=read_json(path);label=Path(result['config']['task']['path']).name
        assert label in planned and label not in seen;seen.add(label)
        frozen=batch/'tasks'/label
        assert tree_hash(frozen)==manifest['tasks'][label]['validation_copy_hash']
        case_path=frozen/'solution/validation-case/case.json';case=read_json(case_path)
        assert file_hash(case_path)==manifest['tasks'][label]['case']['sha256']
        proof_path=path.parent/'agent/case-proof.json'
        proof=read_json(proof_path) if proof_path.exists() else None
        rewards=(result.get('verifier_result') or {}).get('rewards')
        outcome='unresolved_execution';snapshot_refs=[];schema_complete=False
        typed_revision=case.get('comparison_revision')=='v2-case-insensitive-metadata-and-required-types'
        if proof and typed_revision:
            assert proof['case']==planned[label]['case_mode'] and not proof['tested_checker_invoked_to_define_validity']
            assert set(proof['input_types'])==set(proof['expected_columns'])-{'order_date'}
            schema_complete=True
            input_path=path.parent/'agent/orders-input.json'
            assert file_hash(input_path)==case['pristine_input_sha256']==proof['input_artifacts'][0]['sha256']
            data=read_json(input_path)
            expected=derive(data['rows'])
            for ref in proof['submission_artifacts']:
                if not ref['path'].endswith('/snapshot.json'):continue
                relative=ref['path'].removeprefix('/logs/agent/')
                snap_path=path.parent/'agent'/relative
                assert file_hash(snap_path)==ref['sha256']
                snap=read_json(snap_path)
                for name,source in snap['source_files'].items():
                    assert file_hash(snap_path.parent/'submission'/name)==source['sha256']
                actual=compare(expected,snap,proof['expected_columns'],proof['input_types'])
                assert actual==proof['comparison']
                snapshot_refs.append(evidence_ref(snap_path,ROOT))
            assert snapshot_refs
            validity={'witness':'built_from_inputs','equivalent':'legal_equivalent','negative':'business_error'}[case['kind']]
            assert proof['validity']==validity
            if result.get('exception_info') is None and rewards=={'reward':1.0}:
                outcome='missed_wrong' if case['kind']=='negative' else 'accepted_correct'
            elif result.get('exception_info') is None and rewards=={'reward':0.0}:
                outcome='detected_wrong' if case['kind']=='negative' else 'rejected_legal'
        elif proof:
            outcome='historical_case_tooling_not_certified'
        trials.append({**planned[label],'trial_id':result['id'],'raw_directory':str(path.parent.relative_to(ROOT)),
            'case':evidence_ref(case_path,ROOT),'kind':case['kind'],'comparison_revision':case.get('comparison_revision','v1-incomplete-metadata'),
            'schema_complete':schema_complete,'result':evidence_ref(path,ROOT),'native_rewards':rewards,
            'exception_info':result.get('exception_info'),'case_outcome':outcome,
            'proof':evidence_ref(proof_path,ROOT) if proof else None,
            'requirement_ids':proof['requirement_ids'] if proof else [],
            'proof_validity':proof['validity'] if proof else None,
            'violation_kind':proof.get('violation_kind') if proof else None,
            'snapshots':snapshot_refs,
            'logs':[evidence_ref(p,ROOT) for p in [path.parent/'agent/oracle.txt',path.parent/'verifier/test-stdout.txt'] if p.exists()]})
    missing=sorted(set(planned)-seen)
    report={'planned_trials':len(planned),'observed_trials':len(trials),'missing':missing,
        'coverage_complete':not missing,'manifest':evidence_ref(batch/'manifest.json',ROOT),
        'outcomes':dict(Counter(t['case_outcome'] for t in trials)),'trials':trials,
        'scope':'Case-level outcomes only; historical tooling failures remain separate and retain raw rewards'}
    write_json(batch/'reconciliation.json',report)
    print({k:report[k] for k in ['planned_trials','observed_trials','missing','outcomes']})
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('batch',type=Path);args=parser.parse_args()
    report=reconcile(args.batch.resolve())
    raise SystemExit(0 if report['coverage_complete'] else 1)
