"""Validate direct-failure evaluator on preserved real controls plus absent capture."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_ade_failure_score_v3 import score


def validate(directory,output):
    directory,output=Path(directory),Path(output)
    if output.exists():raise ValueError('Preserve evaluator validation evidence')
    output.mkdir(parents=True)
    # This is an explicitly synthetic missing-capture input, not a new trial.
    fake=output/'synthetic-missing-capture';(fake/'batch').mkdir(parents=True);(fake/'trial').mkdir()
    source=directory/'invalid-sql-conformance-v1/rebuild'
    original_binding=io.read_json(source/'source-binding.json');binding=deepcopy(original_binding)
    shutil.copyfile(ROOT/binding['original_result']['path'],fake/'trial/result.json')
    binding['original_result']=io.evidence_ref(fake/'trial/result.json',ROOT)
    io.write_json(fake/'source-binding.json',binding)
    manifest=io.read_json(source/'batch/manifest.json');manifest['source_binding']=io.evidence_ref(fake/'source-binding.json',ROOT)
    io.write_json(fake/'batch/manifest.json',manifest)
    cases=[('healthy_source',directory/'source-only-ana005-v1/rebuild',None,'not_zero'),
           ('missing_capture',fake,None,'not_zero'),
           ('invalid_sql',source,directory/'invalid-sql-conformance-v1/independent-parser-v2/manifest.json','zero'),
           ('missing_model',directory/'missing-model-conformance-v1/rebuild',None,'zero')]
    controls=[]
    for name,audit,diagnostic,expected in cases:
        observation={'id':name,'expected':expected,'fixture_only':True,'model_requests':0,
                     'source_binding':io.evidence_ref(audit/'source-binding.json',ROOT),
                     'synthetic_incomplete_capture':name=='missing_capture'}
        try:
            value=score(audit,output/(name+'-evaluation'),diagnostic,validation_only=True)
            observation.update(observed='zero'if value['audit_reward']==0 else 'not_zero',
                evaluator_output=io.evidence_ref(output/(name+'-evaluation')/'validation-observation.json',ROOT))
        except Exception as exc:
            observation.update(observed='not_zero',exception={'type':type(exc).__name__,'message':str(exc)})
            (output/(name+'-exception.txt')).write_text(traceback.format_exc())
            observation['exception_evidence']=io.evidence_ref(output/(name+'-exception.txt'),ROOT)
        passed=observation['observed']==expected
        if name=='healthy_source':passed &= observation.get('exception',{}).get('message')=='This bounded failure path requires an actual original native zero'
        if name=='missing_capture':passed &= observation.get('exception',{}).get('type')=='FileNotFoundError'and str(fake/'trial/artifacts/manifest.json')in observation['exception']['message']
        observation['passed']=bool(passed)
        io.write_json(output/(name+'.json'),observation)
        controls.append({k:observation[k]for k in ('id','expected','observed','passed')}|{'evidence':[io.evidence_ref(output/(name+'.json'),ROOT)]})
    def check(names):
        values=[c for c in controls if c['id']in names]
        return {'status':'pass'if all(c['passed']for c in values)else'fail','evidence':[r for c in values for r in c['evidence']]}
    result={'schema_version':1,'kind':'failure_evaluator_validation',
        'evaluator':io.evidence_ref(ROOT/'scripts/difficulty_ade_failure_score_v3.py',ROOT),
        'validator':io.evidence_ref(Path(__file__),ROOT),'controls':controls,
        'checks':{'healthy_submission_rejected_as_failure':check({'healthy_source'}),
                  'incomplete_capture_rejected':check({'missing_capture'}),
                  'contract_failure_detected':check({'invalid_sql','missing_model'})},
        'complete':all(c['passed']for c in controls),'model_requests':0,'fixture_only':True,
        'training_prohibited':True,'trial_reruns':0}
    io.write_json(output/'validation.json',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(validate(a.directory,a.output)['checks'])
