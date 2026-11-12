"""Prepare immutable reviewed case copies with the native read-only collector.

Does not regenerate canonical cases or run jobs. Harbor owns execution.
"""
import argparse
from pathlib import Path
import shutil
import sys

HERE=Path(__file__).resolve().parent;TASK=HERE.parent;ROOT=TASK.parents[2]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.backends import binding_identity,configure_backend,resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref,read_json,tree_hash,write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--case',action='append',help='Canonical case directory name; omit for every reviewed case')
    parser.add_argument('--concurrent',type=int,default=3)
    args=parser.parse_args()
    if args.concurrent<1:raise ValueError('Concurrency must be positive')
    contract=read_json(TASK/'contract.yaml')
    if not contract['audit']['checker_version'].startswith('customer-output-audit-v2:'):
        raise ValueError('Current contract does not select audit v2')
    cases={p.parent.name:(p,read_json(p)) for p in (TASK/'cases').glob('*/case.json')}
    selected=args.case if args.case else sorted(k for k,(_,c) in cases.items() if c.get('reviewed'))
    if not selected or len(selected)!=len(set(selected)):raise ValueError('Require distinct reviewed cases')
    if any(k not in cases or not cases[k][1].get('reviewed') for k in selected):
        raise ValueError('Unknown or superseded case; canonical inputs remain unchanged')
    key=contract['task_key'];row=next(r for r in load_catalog(ROOT) if r['task_key']==key)
    native=resolve_task(ROOT,row,'apptainer')
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    config=read_json(ROOT/'configs/validation.yaml');configure_backend(config,'apptainer')
    config.update(job_name=out.name,jobs_dir=str(out/'jobs'),n_attempts=1,n_concurrent_trials=args.concurrent,
                  agents=[{'name':'oracle','env':{'DB_TYPE':'duckdb','PROJECT_TYPE':'dbt'}}],tasks=[])
    records={}
    for name in selected:
        case_path,case=cases[name];label=key+'--'+name;task=out/'tasks'/label
        shutil.copytree(native,task,symlinks=True);_wrap_case(task,case_path.parent,case)
        tests=task/'tests';(tests/'test.sh').rename(tests/'proof-native-test.sh')
        (tests/'test.sh').write_text('#!/bin/bash\npython3 /tests/customer-collector.py before || true\nexec bash /tests/proof-native-test.sh\n')
        (tests/'test-setup.sh').rename(tests/'native-test-setup.sh')
        (tests/'test-setup.sh').write_text('#!/bin/bash\nbash /tests/native-test-setup.sh\nsetup_status=$?\npython3 /tests/customer-collector.py after-native-build "$setup_status" || true\nexit "$setup_status"\n')
        shutil.copyfile(TASK/'collect_audit.py',tests/'customer-collector.py')
        config['tasks'].append({'path':str(task)})
        records[label]={'case':evidence_ref(case_path,ROOT),'case_payload_hash':tree_hash(case_path.parent),
            'task_hash':tree_hash(task),'native_test':evidence_ref(tests/'native-test.sh',ROOT),
            'native_setup':evidence_ref(tests/'native-test-setup.sh',ROOT)}
    write_json(out/'config.yaml',check_harbor(config,[Path(t['path']) for t in config['tasks']]))
    write_json(out/'manifest.json',{'schema_version':1,'task_key':key,'planned_trials':len(selected),'tasks':records,
        'backend_binding':binding_identity(ROOT,row,'apptainer'),'contract':evidence_ref(TASK/'contract.yaml',ROOT),
        'config':evidence_ref(out/'config.yaml',ROOT),'preparer':evidence_ref(Path(__file__),ROOT),
        'checker_version':contract['audit']['checker_version'],
        'excluded_unreviewed_case_ids':sorted(k for k,(_,c) in cases.items() if not c.get('reviewed')),
        'native_verifier_modified':False,'canonical_case_files_modified':False,'model_requests':0})
    print({'planned_trials':len(selected),'config':str(out/'config.yaml')})


if __name__=='__main__':main()
