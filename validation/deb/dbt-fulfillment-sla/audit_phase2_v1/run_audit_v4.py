"""CLI for hash-pinned retained or fresh task snapshots and original raw inputs."""
import argparse, hashlib, json
from pathlib import Path
import yaml
from audit_tables import CaptureError, decode, load_observer_table
from expected_v2 import build
from audit_v2 import evaluate, check
from workflow_v3 import evaluate as workflow_evaluate, MODELS, PROJECT
KIND='fulfillment'
TASK='deb__dbt-fulfillment-sla'

def checked(ref,root):
    p=root/ref['path'];b=p.read_bytes()
    if hashlib.sha256(b).hexdigest()!=ref['sha256']:raise CaptureError('Changed evidence '+str(p))
    return b

def decode_recursive(x):
    if isinstance(x,dict):return decode(x)if'$type'in x else{k:decode_recursive(v)for k,v in x.items()}
    if isinstance(x,list):return[decode_recursive(v)for v in x]
    return x

def read_snapshot(spec,root,names):
    sr=spec['candidate_snapshot'];p=root/sr['path'];s=json.loads(checked(sr,root))
    if s.get('task_key')!=TASK or s.get('stage')!=spec['candidate_stage']:raise CaptureError('Wrong task or stage')
    outputs={};limits=[]
    for name in names:
        relation=('fraud_analytics.'if name=='fraud_flagged_orders'else'main.')+name
        items=[x for x in s['outputs']if x['specification']['relation']==relation and x['specification']['database']=='/app/database/retail.duckdb']
        if len(items)!=1:raise CaptureError('Required declared relation not uniquely captured: '+relation)
        x=items[0];result={};error=x.get('error')
        if error:
            msg=error.get('message','')
            if error.get('type')=='CatalogException'and ('Table with name '+name+' does not exist'in msg or'Schema with name '+relation.split('.')[0]+' does not exist'in msg):result['exists']=False
        else:
            fn=x['file']
            if Path(fn).name!=fn:raise CaptureError('Nonlocal capture file')
            data=checked({'path':str(p.parent/fn),'sha256':x['sha256']},root);cols=[c['name']for c in x['schema']]
            if len({c.lower()for c in cols})!=len(cols):raise CaptureError('Duplicate columns')
            rows=[json.loads(line)for line in data.splitlines()]
            if len(rows)!=x['rows']or any(len(r)!=len(cols)for r in rows):raise CaptureError('Invalid row capture')
            result['rows']=[dict(zip(cols,map(decode,row)))for row in rows]
            result['sql_types']={c['name'].lower():c['type']for c in x.get('sql_schema',[])}
            try:result['table']=load_observer_table(sr,task_key=TASK,database='/app/database/retail.duckdb',relation=relation,stage=spec['candidate_stage'],root=root)
            except CaptureError as e:limits.append({'relation':relation,'reason':str(e)})
        outputs[name]=result
    files={}
    for f in s.get('files',[]):
        if f.get('exists')and'text'in f:
            if hashlib.sha256(f['text'].encode()).hexdigest()!=f['sha256']:raise CaptureError('Captured source hash mismatch')
            files[f['path']]=f['text']
        elif f.get('exists')is False:files[f['path']]=None
    errors=[]
    # A captured absence proves a defect only for an explicitly prescribed path.
    if KIND=='fulfillment':
        for path in MODELS[KIND].values():
            full=PROJECT+'/'+path
            if full in files and files[full]is None:errors.append('Required model file absent: '+full)
    full=PROJECT+'/profiles.yml'
    if full in files:
        try:
            profile=yaml.safe_load(files[full])
            if not isinstance(profile,dict)or'retail_dw_master'not in profile:errors.append('Required retail_dw_master profile absent')
        except (TypeError,yaml.YAMLError):errors.append('Captured profiles.yml is absent or invalid YAML')
    return outputs,limits,check('fail','; '.join(errors))if errors else None

def compiled_rows(spec,root):
    if not spec.get('compiled_capture'):return None
    cr=spec['compiled_capture'];p=root/cr['path'];capture=json.loads(checked(cr,root))
    if capture.get('capture_error')or capture.get('database_and_wal_unchanged')is not True:raise CaptureError('Compiled capture failed or changed database')
    if capture.get('kind')!=KIND or capture.get('phase')!='final':raise CaptureError('Wrong compiled capture task/phase')
    result={}
    for name,c in capture.get('compiled',{}).items():
        if c.get('statement_types')!=['StatementType.SELECT']:raise CaptureError('Compiled capture not single SELECT')
        fn=Path(c['path']).name;data=checked({'path':str(p.parent/fn),'sha256':c['sha256']},root)
        sql=c['sql'];checked({'path':str(p.parent/Path(sql['path']).name),'sha256':sql['sha256']},root)
        rows=[json.loads(line)for line in data.splitlines()]
        if len(rows)!=c['rows']or any(len(row)!=len(c['columns'])for row in rows):raise CaptureError('Compiled row capture width/count mismatch')
        result[name]=[dict(zip(c['columns'],map(decode,row)))for row in rows]
    return result

def run(spec,root):
    try:
        if spec.get('task_key')!=TASK:raise CaptureError('Wrong audit task')
        raw=json.loads(checked(spec['original_input'],root))
        for ref in raw['source_refs']:checked(ref,root)
        source=decode_recursive(raw['rows'])
        if KIND=='fraud':expected,facts=build(source);variants=[expected]
        else:
            from expected_v2 import build_variants
            variants,facts=build_variants(source)
        outputs,limits,local_failure=read_snapshot(spec,root,variants[0]);wf=None
        if spec.get('trusted_workflow'):wf=workflow_evaluate(KIND,json.loads(checked(spec['trusted_workflow'],root)))
        if local_failure:wf=local_failure
        result=evaluate(KIND,outputs,variants,workflow=wf,compiled=compiled_rows(spec,root));result.update(task_key=TASK,statistics=facts,capture_limitations=limits,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v});return result
    except(OSError,ValueError,KeyError,TypeError,AssertionError,ArithmeticError,AttributeError,yaml.YAMLError)as e:
        return {'task_key':TASK,'status':'unknown','reward':None,'reason':str(e),'error_type':type(e).__name__,'model_requests':0,'native_rewards_modified':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();r=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'audit_reward':r['reward']}))
