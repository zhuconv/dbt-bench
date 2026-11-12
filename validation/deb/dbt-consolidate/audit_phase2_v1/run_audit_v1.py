"""Offline audit CLI over hash-verified raw inputs and actual captured submissions."""
import argparse,csv,hashlib,io,json
from decimal import Decimal
from pathlib import Path
from audit_tables import CaptureError,decode,load_observer_table
from expected_v1 import SOURCES,TABLES,derive
from audit_v1 import evaluate,check
TASK='deb__dbt-consolidate'
def checked(ref,root):
    p=root/ref['path'];data=p.read_bytes()
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise CaptureError('Changed evidence: '+str(p))
    return data
def inputs(spec,root):
    original=json.loads(checked(spec['original_input'],root));raw={}
    for name,(_,views)in SOURCES.items():
        reader=csv.DictReader(io.StringIO(checked(original['csv'][name],root).decode()))
        if set(reader.fieldnames or())!={'ad_date','clicks','impressions','conversions',*views}:raise CaptureError('Unexpected public CSV schema')
        raw[name]=list(reader)
    return derive(raw)
def read_snapshot(spec,root):
    sr=spec['candidate_snapshot'];p=root/sr['path'];s=json.loads(checked(sr,root));outputs={};limits=[]
    if spec.get('capture_format')=='legacy_consolidate':
        for name in TABLES:
            o=s['tables'].get(name)
            if not o:continue
            rows=[]
            for row in o['rows']:
                r=dict(row)
                for c,kind in o['types'].items():
                    if kind=='NUMBER'and isinstance(r.get(c),str):r[c]=Decimal(r[c])
                rows.append(r)
            outputs[name]={'rows':rows,'relation_type':o.get('table_type')};limits.append({'relation':TABLES[name],'reason':'Legacy exact rows and DB-API types only; native physical SQL types were not captured'})
        return outputs,limits
    if s.get('task_key')!=TASK or s.get('stage')!=spec['candidate_stage']:raise CaptureError('Wrong task/stage')
    for name,table in TABLES.items():
        relation='analytics.'+table;matches=[x for x in s['outputs']if x['specification']['relation']==relation and x['specification']['database']=='/app/consolidate.duckdb']
        if len(matches)!=1:raise CaptureError('Required relation not uniquely observed')
        x=matches[0];o={}
        if isinstance(x.get('relation_sql_schema'),list):
            physical=[c.get('name')for c in x['relation_sql_schema']]
            if any(not isinstance(c,str)for c in physical)or len(physical)!=len(set(physical)):raise CaptureError('Malformed native physical schema')
            o['physical_columns']=physical
        if x.get('error'):
            err=x['error']
            if err.get('type')=='CatalogException'and ('Table with name '+table+' does not exist'in err.get('message','')or'Schema with name analytics does not exist'in err.get('message','')):o['exists']=False
        else:
            fn=x['file']
            if Path(fn).name!=fn:raise CaptureError('Nonlocal captured row filename')
            data=checked({'path':str(p.parent/fn),'sha256':x['sha256']},root);cols=[c['name']for c in x['schema']];rows=[json.loads(line)for line in data.splitlines()]
            if len(cols)!=len(set(cols))or len(rows)!=x['rows']or any(len(r)!=len(cols)for r in rows):raise CaptureError('Malformed captured rows')
            o['rows']=[dict(zip(cols,map(decode,r)))for r in rows]
            try:o['table']=load_observer_table(sr,task_key=TASK,database='/app/consolidate.duckdb',relation=relation,stage=spec['candidate_stage'],root=root)
            except CaptureError as e:limits.append(dict(relation=relation,reason=str(e)))
        outputs[name]=o
    return outputs,limits
def run(spec,root):
    try:
        if spec.get('task_key')!=TASK:raise CaptureError('Wrong audit task')
        expected=inputs(spec,root);outputs,limits=read_snapshot(spec,root);workflow=None
        if spec.get('trusted_workflow'):
            from workflow_v1 import evaluate as wf
            facts=json.loads(checked(spec['trusted_workflow'],root))
            for r in facts.get('evidence',[]):checked(r,root)
            workflow=wf(facts)
        if spec.get('capture_format')!='legacy_consolidate':
            snapshot=json.loads(checked(spec['candidate_snapshot'],root));files=snapshot.get('files',[]);required=['/app/dbt_consolidate/profiles.yml','/app/dbt_consolidate/dbt_project.yml']
            missing=[f['path']for f in files if f['path']in required and f.get('exists')is False]
            if missing:workflow=check('fail','Trusted observation explicitly proves required local project files absent',missing=missing)
        result=evaluate(outputs,expected,workflow);result.update(task_key=TASK,capture_limitations=limits,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v});return result
    except(OSError,ValueError,KeyError,TypeError,AssertionError,ArithmeticError,AttributeError)as e:return dict(task_key=TASK,status='unknown',reward=None,reason=str(e),error_type=type(e).__name__,model_requests=0,native_rewards_modified=False)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();r=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'audit_reward':r['reward']}))
