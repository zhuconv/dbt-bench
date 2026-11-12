"""Hash-verified raw input and retained observer audit CLI."""
import argparse,hashlib,json
from pathlib import Path
from audit_tables import CaptureError,decode,load_observer_table
from expected_v2 import derive,COLS
from audit_v2 import evaluate,check
TASK='deb__dbt-channel-attribution-analysis'
def checked(ref,root):
    p=root/ref['path'];data=p.read_bytes()
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise CaptureError('Changed evidence '+str(p))
    return data
def recursive(v):
    if isinstance(v,dict):return decode(v)if'$type'in v else{k:recursive(x)for k,x in v.items()}
    if isinstance(v,list):return[recursive(x)for x in v]
    return v
def read_snapshot(spec,root):
    sr=spec['candidate_snapshot'];p=root/sr['path'];s=json.loads(checked(sr,root))
    if s.get('task_key')!=TASK or s.get('stage')!=spec['candidate_stage']:raise CaptureError('Wrong task/stage')
    outputs={};limits=[]
    for name in COLS:
        relation='main_channel_analytics.'+name;matches=[x for x in s['outputs']if x['specification']['relation']==relation and x['specification']['database']=='/app/database/retail.duckdb']
        if len(matches)!=1:raise CaptureError('Required relation not uniquely captured')
        x=matches[0];o={}
        if isinstance(x.get('relation_sql_schema'),list):
            physical=[c.get('name')for c in x['relation_sql_schema']]
            if any(not isinstance(c,str)for c in physical)or len(physical)!=len(set(physical)):raise CaptureError('Malformed physical schema')
            o['physical_columns']=physical
        if x.get('error'):
            err=x['error']
            if err.get('type')=='CatalogException'and('Table with name '+name+' does not exist'in err.get('message','')or'Schema with name main_channel_analytics does not exist'in err.get('message','')):o['exists']=False
        else:
            fn=x['file']
            if Path(fn).name!=fn:raise CaptureError('Nonlocal captured row filename')
            data=checked({'path':str(p.parent/fn),'sha256':x['sha256']},root);cols=[c['name']for c in x['schema']];rows=[json.loads(line)for line in data.splitlines()]
            if len(cols)!=len(set(cols))or len(rows)!=x['rows']or any(len(r)!=len(cols)for r in rows):raise CaptureError('Malformed row capture')
            o['rows']=[dict(zip(cols,map(decode,row)))for row in rows]
            try:o['table']=load_observer_table(sr,task_key=TASK,database='/app/database/retail.duckdb',relation=relation,stage=spec['candidate_stage'],root=root)
            except CaptureError as e:limits.append(dict(relation=relation,reason=str(e)))
        outputs[name]=o
    return outputs,limits
def run(spec,root):
    try:
        if spec.get('task_key')!=TASK:raise CaptureError('Wrong audit task')
        original=json.loads(checked(spec['original_input'],root))
        for r in original['source_refs']:checked(r,root)
        expected,facts=derive(recursive(original['rows']));outputs,limits=read_snapshot(spec,root);workflow=None;compiled=None
        if spec.get('trusted_workflow'):
            from workflow_v3 import evaluate as wf
            w=json.loads(checked(spec['trusted_workflow'],root))
            for r in w.get('evidence',[]):checked(r,root)
            workflow=wf(w)
        if spec.get('compiled_output'):
            x=json.loads(checked(spec['compiled_output'],root))
            for r in x['source_refs']:checked(r,root)
            compiled=recursive(x['rows'])
        snapshot=json.loads(checked(spec['candidate_snapshot'],root));files={}
        for f in snapshot.get('files',[]):
            if f.get('exists')and'text'in f:
                if hashlib.sha256(f['text'].encode()).hexdigest()!=f['sha256']:raise CaptureError('Source text hash mismatch')
                files[f['path']]=f['text']
            elif f.get('exists')is False:files[f['path']]=None
        required=['/app/dbt_models_duckdb/profiles.yml',*['/app/dbt_models_duckdb/'+p for p in ['models/staging/stg_orders__channels.sql','models/intermediate/int_customer_channel_first_touch.sql','models/marts/channel_performance.sql']]];missing=[p for p in required if p in files and files[p]is None]
        if missing:workflow=check('fail','Actual required submission files absent',missing=missing)
        result=evaluate(outputs,expected,workflow,compiled,facts['rounding_half_ties']);result.update(task_key=TASK,statistics=facts,capture_limitations=limits,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v});return result
    except(OSError,ValueError,KeyError,TypeError,AssertionError,ArithmeticError,AttributeError)as e:return dict(task_key=TASK,status='unknown',reward=None,reason=str(e),error_type=type(e).__name__,model_requests=0,native_rewards_modified=False)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();r=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'audit_reward':r['reward']}))
