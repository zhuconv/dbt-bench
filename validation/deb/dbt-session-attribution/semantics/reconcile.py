"""Verify saved inputs/proofs, classify native outcomes, and preserve fixture scope."""
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref,file_hash,read_json,tree_hash,write_json
from independent import calculate,compare,encoded


def reconcile(batch):
    batch=batch.resolve();manifest=read_json(batch/'manifest.json');planned={p['task_label']:p for p in manifest['planned']}
    seen=set();ids=set();trials=[];positive_outputs=defaultdict(list)
    for path in sorted((batch/'jobs').glob('*/*/result.json')):
        raw=read_json(path);label=Path(raw['config']['task']['path']).name
        assert label in planned and label not in seen and raw['id'] not in ids
        seen.add(label);ids.add(raw['id']);entry=manifest['tasks'][label]
        assert tree_hash(batch/'tasks'/label)==entry['validation_copy_hash']
        case_path=ROOT/entry['case']['path'];assert file_hash(case_path)==entry['case']['sha256']
        assert tree_hash(case_path.parent)==entry['case_hash'];case=read_json(case_path)
        proof_path=path.parent/'agent/case-proof.json';proof=read_json(proof_path) if proof_path.exists() else None
        proof_valid=False;comp=None
        if proof:
            assert proof['case']==case['mode'] and proof['data_scope']==case['data_scope']
            assert proof['tested_checker_invoked_to_define_validity'] is False
            for field in ['input_artifacts','submission_artifacts']:
                for ref in proof[field]:
                    if ref['path'].startswith('/logs/agent/'):
                        p=path.parent/'agent'/ref['path'].removeprefix('/logs/agent/')
                    else:
                        assert ref['path'].endswith('/models/marts/attribution_report.sql')
                        p=path.parent/'agent/after-case/models/marts/attribution_report.sql'
                    assert file_hash(p)==ref['sha256']
            inputs=read_json(path.parent/'agent/evaluated-inputs.json');expected=calculate(inputs)
            assert not expected['unresolved'] and expected==read_json(path.parent/'agent/independent-expectation.json')
            for label in ['original-output','before-case','after-case']:
                snap_path=path.parent/'agent'/label/'snapshot.json';snapshot=read_json(snap_path)
                for ref in snapshot['files']:
                    rel=ref['path'].split('/'+label+'/',1)[1];assert file_hash(snap_path.parent/rel)==ref['sha256']
                wanted=(calculate(read_json(path.parent/'agent/original-inputs.json')) if label=='original-output' else expected)
                current=compare(wanted['expected'],snapshot)
                if label!='after-case':assert current['equal']
                else:
                    assert current==proof['facts']['comparison'];comp=current
                    assert current['equal']==(case['kind']!='negative')
                    if case['mode'].endswith('-reference'):
                        positive_outputs[case['mode']].append(sorted(encoded(r) for r in snapshot['rows']))
            proof_valid=True
        rewards=(raw.get('verifier_result') or {}).get('rewards');exception=raw.get('exception_info')
        log_path=path.parent/'verifier/test-stdout.txt';log=log_path.read_text() if log_path.exists() else ''
        outcome='unresolved_execution'
        if proof_valid and exception is None:
            if rewards=={'reward':1.0}:
                outcome='missed_wrong' if case['kind']=='negative' else 'accepted_correct'
            elif rewards=={'reward':0.0} and 'AssertionError:' in log and 'ERROR at setup' not in log:
                outcome='detected_wrong' if case['kind']=='negative' else 'rejected_legal'
        trials.append({**planned[Path(raw['config']['task']['path']).name],'trial_id':raw['id'],'kind':case['kind'],
                       'requirements':case['requirement_ids'],'outcome':outcome,'native_rewards':rewards,'exception_info':exception,
                       'independent_proof':evidence_ref(proof_path,ROOT) if proof else None,'proof_recomputed':proof_valid,
                       'result':evidence_ref(path,ROOT),'native_log':evidence_ref(log_path,ROOT) if log_path.exists() else None})
    report={'schema_version':1,'planned':len(planned),'observed':len(trials),'missing':sorted(set(planned)-seen),
            'complete':set(planned)==seen,'outcomes':dict(Counter(t['outcome'] for t in trials)),
            'data_scopes':dict(Counter(t['data_scope'] for t in trials)),
            'positive_stability':{k:{'runs':len(v),'equal':len(v)>=2 and all(rows==v[0] for rows in v)} for k,v in positive_outputs.items()},
            'trials':trials,'full_validation_complete':False,
            'scope':'Actual native verifier on explicitly replaced fixture input relations is reported separately from original-data reference scores.'}
    out=batch/'reconciliation.json';assert not out.exists();write_json(out,report)
    print({k:v for k,v in report.items() if k not in ['trials','scope']})
    return 0 if report['complete'] and not any(t['outcome']=='unresolved_execution' for t in trials) else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True)
    raise SystemExit(reconcile(p.parse_args().batch))
