"""Freeze customer cases plus collection-only native-build instrumentation.

Run the emitted configuration with Harbor; this command does not schedule jobs.
"""
from pathlib import Path
import argparse,json,shutil,sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'src'))
from case_engine import MODES
from dbt_bench.backends import binding_identity,configure_backend,resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref,file_hash,read_json,tree_hash,write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--reference-nop',action='store_true',help='Two native oracle and two native nop controls with the same collection sequence')
    args=parser.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    key='ade__ade-bench-ana-eng003'
    row=next(r for r in load_catalog(ROOT) if r['task_key']==key)
    template=read_json(HERE/'cases/reference/case.json')
    for mode,(kind,requirements) in ({} if args.reference_nop else MODES).items():
        directory=HERE/'cases'/mode;directory.mkdir(exist_ok=True)
        case={**template,'kind':kind,'mode':mode,'requirement_ids':requirements,
              'engine_sha256':file_hash(HERE/'case_engine.py'),
              'case_revision':'customer-source-workflow-v2'}
        write_json(directory/'case.json',case)
        shutil.copyfile(HERE/'case_engine.py',directory/'case_engine.py')
        (directory/'submission.sh').write_text('#!/bin/bash\nset -euo pipefail\npython3 /solution/validation-case/case_engine.py '+mode+'\n')
        (directory/'proof.sh').write_text('#!/bin/bash\nset -euo pipefail\ntest -s "$DBT_BENCH_PROOF"\n')
    config=read_json(ROOT/'configs/validation.yaml');configure_backend(config,'apptainer')
    config.update(job_name=out.name,jobs_dir=str(out/'jobs'),n_concurrent_trials=3,n_attempts=1,
                  agents=[{'name':'oracle','env':{'DB_TYPE':'duckdb'}}],tasks=[])
    selected=[('reference',1),('reference',2)]+[(m,1) for m in MODES if m!='reference']
    if args.reference_nop:
        selected=[('native-control',1)]
        config.update(n_attempts=2,agents=[{'name':a,'env':{'DB_TYPE':'duckdb'}} for a in ['oracle','nop']])
    records={};planned=[]
    for mode,repetition in selected:
        case_file=None if args.reference_nop else HERE/'cases'/mode/'case.json'
        case=read_json(case_file) if case_file else None
        label=f'{key}--{mode}--{repetition}';task=out/'tasks'/label
        shutil.copytree(resolve_task(ROOT,row,'apptainer'),task,symlinks=True)
        if case_file:_wrap_case(task,case_file.parent,case)
        tests=task/'tests'
        if not case_file:shutil.copyfile(tests/'test.sh',tests/'native-test.sh')
        (tests/'test.sh').rename(tests/'proof-native-test.sh')
        (tests/'test.sh').write_text('#!/bin/bash\npython3 /tests/customer-collector.py before || true\nexec bash /tests/proof-native-test.sh\n')
        (tests/'test-setup.sh').rename(tests/'native-test-setup.sh')
        (tests/'test-setup.sh').write_text('#!/bin/bash\nbash /tests/native-test-setup.sh\nsetup_status=$?\npython3 /tests/customer-collector.py after-native-build "$setup_status" || true\nexit "$setup_status"\n')
        shutil.copyfile(HERE/'collect_audit.py',tests/'customer-collector.py')
        config['tasks'].append({'path':str(task)})
        records[label]={'task_key':key,'case_mode':mode,'repetition':repetition,
            'original_task_hash':row['adapted_task_hash'],'validation_copy_hash':tree_hash(task),
            'backend_binding':binding_identity(ROOT,row,'apptainer'),'case':evidence_ref(case_file,ROOT) if case_file else None,
            'case_payload_hash':tree_hash(case_file.parent) if case_file else None,'native_verifier':evidence_ref(tests/'native-test.sh',ROOT),
            'native_setup':evidence_ref(tests/'native-test-setup.sh',ROOT)}
        for agent in (['oracle','nop'] if args.reference_nop else ['oracle']):
            for attempt in ([1,2] if args.reference_nop else [1]):
                planned.append({'task_key':key,'task_label':label,'case_mode':mode,'reference_repetition':repetition,'agent':agent,'attempt':attempt})
    write_json(out/'config.yaml',check_harbor(config,[Path(t['path']) for t in config['tasks']]))
    shutil.copyfile(ROOT/'sources.lock.yaml',out/'sources.lock.yaml')
    shutil.copyfile(ROOT/'runtime/uv.lock',out/'runtime-uv.lock')
    for name in ['case_engine.py','collect_audit.py','audit.py','prepare_audit_cases.py']:
        shutil.copyfile(HERE/name,out/name)
    write_json(out/'manifest.json',{'schema_version':1,'kind':'native_customer_cases_with_read_only_audit_collection',
        'backend':'apptainer','tasks':records,'planned':planned,'config':evidence_ref(out/'config.yaml',ROOT),
        'source_lock':evidence_ref(out/'sources.lock.yaml',ROOT),'runtime_lock':evidence_ref(out/'runtime-uv.lock',ROOT),
        'collector':evidence_ref(out/'collect_audit.py',ROOT),'audit':evidence_ref(out/'audit.py',ROOT),
        'collection_order':['submission and independent proof','read-only pre-native export','unchanged native dbt setup',
                            'read-only build export','unchanged native hidden tests','offline read-only audit'],
        'native_score_overwritten':False,'automatic_certification':False,'qwen_smoke':False})
    print(json.dumps({'planned':len(planned),'config':str(out/'config.yaml')}))


if __name__=='__main__':main()
