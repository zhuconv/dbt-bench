"""Prepare explicit session semantic cases; execution stays with native Harbor."""
import argparse,hashlib,json,shutil,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.backends import binding_identity,configure_backend,resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref,file_hash,read_json,tree_hash,write_json
from dbt_bench.prepare import check_harbor
from dbt_bench.validation import _wrap_case
from independent import SESSION_COLUMNS,SALES_COLUMNS,encoded,canonical

# Keep the plan independent of duckdb, which is only required inside task containers.
PLAN={
 'original-reference':('witness',['R-conversion','R-single-touch','R-idempotence']),
 'fixture-reference':('witness',['R-channel-priority','R-quality','R-window','R-position','R-decay','R-normalization','R-bonus']),
 'fixture-extra-null-column':('equivalent',['R-output-location']),
 'fixture-paid-priority':('negative',['R-channel-priority']),
 'fixture-duration':('negative',['R-quality']), 'fixture-pageviews':('negative',['R-quality']), 'fixture-bot':('negative',['R-quality']),
 'fixture-upper-bound':('negative',['R-window']), 'fixture-lower-bound':('negative',['R-window']),
 'fixture-position':('negative',['R-position']), 'fixture-flat-decay':('negative',['R-decay']), 'fixture-integer-days':('negative',['R-decay']),
 'fixture-global-normalization':('negative',['R-normalization']), 'fixture-no-bonus':('negative',['R-bonus']), 'fixture-credit-bonus':('negative',['R-bonus']),
 'fixture-revenue-max':('negative',['R-conversion']), 'fixture-customer-join':('negative',['R-conversion']),
 'fixture-drop-single':('negative',['R-single-touch']), 'fixture-excess-precision':('negative',['R-aggregate-round'])}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--case',action='append',choices=list(PLAN));p.add_argument('--concurrent',type=int,default=3);args=p.parse_args()
    selected=args.case or list(PLAN);assert len(set(selected))==len(selected) and args.concurrent>0
    key='deb__dbt-session-attribution';row=next(r for r in load_catalog(ROOT) if r['task_key']==key)
    initial=read_json(ROOT/'artifacts/validation/session-semantics-v2/initial.json')
    data=canonical({key:[{c:r[c] for c in cols} for r in initial['inputs'][table]['rows']]
                    for key,table,cols in [('sessions','stg_digital__web_sessions',SESSION_COLUMNS),('sales','stg_analytics__fact_sales',SALES_COLUMNS)]})
    input_hash=hashlib.sha256((encoded(data)+'\n').encode()).hexdigest()
    solve=(ROOT/row['run_path']/'solution/solve.sh').read_text()
    sql=solve.split("cat > models/marts/attribution_report.sql << 'EOF'\n",1)[1].split('\nEOF',1)[0]+'\n'
    model_hash=hashlib.sha256(sql.encode()).hexdigest()
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    config=read_json(ROOT/'configs/validation.yaml');configure_backend(config,'apptainer')
    config.update(job_name=out.name,jobs_dir=str(out/'jobs'),n_attempts=1,n_concurrent_trials=args.concurrent,
                  agents=[{'name':'oracle','env':{'DB_TYPE':'duckdb'}}],tasks=[])
    planned=[];records={};base=Path(__file__).parent
    for mode in selected:
        kind,requirements=PLAN[mode]
        case={'schema_version':1,'mode':mode,'kind':kind,'reviewed':True,'requirement_ids':requirements,
              'original_task_hash':row['adapted_task_hash'],'pristine_inputs_sha256':input_hash,'reference_model_sha256':model_hash,
              'independent_derivation':'Independent Decimal input reconstruction and declared small-data rules; no native grader result defines validity.',
              'submission_script':'submission.sh','proof_script':'proof.sh','run_native_reference':True,
              'data_scope':'original_pinned_inputs' if mode=='original-reference' else 'additional_input_fixture',
              'helpers':{name:file_hash(base/name) for name in ['engine.py','independent.py','fixtures.py']}}
        directory=base.parent/'cases/semantic-v2'/mode
        if directory.exists():
            assert read_json(directory/'case.json')==case,'Existing case differs; use a new case version'
            assert all(file_hash(directory/name)==sha for name,sha in case['helpers'].items())
        else:
            directory.mkdir(parents=True)
            for name in case['helpers']:shutil.copyfile(base/name,directory/name)
            write_json(directory/'case.json',case)
            (directory/'submission.sh').write_text('#!/bin/bash\nset -euo pipefail\npython3 /solution/validation-case/engine.py '+mode+'\n')
            (directory/'proof.sh').write_text('#!/bin/bash\nset -euo pipefail\ntest -s "$DBT_BENCH_PROOF"\n')
        for repetition in range(1,3 if mode.endswith('-reference') else 2):
            label=f'{key}--{mode}--{repetition}';dest=out/'tasks'/label
            shutil.copytree(resolve_task(ROOT,row,'apptainer'),dest,symlinks=True);_wrap_case(dest,directory,case)
            for native,copy in [('tests/test.sh','tests/native-test.sh'),('tests/test_outputs.py','tests/test_outputs.py')]:
                assert file_hash(ROOT/row['run_path']/native)==file_hash(dest/copy)
            config['tasks'].append({'path':str(dest)})
            records[label]={'task_key':key,'case_mode':mode,'repetition':repetition,'data_scope':case['data_scope'],
                            'case':evidence_ref(directory/'case.json',ROOT),'case_hash':tree_hash(directory),
                            'validation_copy_hash':tree_hash(dest),'backend_binding':binding_identity(ROOT,row,'apptainer')}
            planned.append({'task_label':label,'task_key':key,'mode':mode,'repetition':repetition,'data_scope':case['data_scope']})
    config=check_harbor(config,[Path(t['path']) for t in config['tasks']]);write_json(out/'config.yaml',config)
    for dest,source in [('sources.lock.yaml',ROOT/'sources.lock.yaml'),('runtime-uv.lock',ROOT/'runtime/uv.lock'),('runner.py',Path(__file__))]:shutil.copyfile(source,out/dest)
    write_json(out/'manifest.json',{'schema_version':1,'planned':planned,'tasks':records,'config':evidence_ref(out/'config.yaml',ROOT),
          'source_lock':evidence_ref(out/'sources.lock.yaml',ROOT),'runtime_lock':evidence_ref(out/'runtime-uv.lock',ROOT),
          'qwen_smoke':False,'scope':'Explicit original references and additional-input fixture cases; fixture native rewards are not original benchmark scores.'})
    print({'config':str(out/'config.yaml'),'planned':len(planned),'input_hash':input_hash,'reference_model_hash':model_hash})


if __name__=='__main__':main()
