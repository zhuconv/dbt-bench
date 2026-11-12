"""Audit CLI; unknown capture cannot become a positive or negative submission."""
import argparse,hashlib,json
import yaml
from pathlib import Path
from audit_tables import CaptureError,decode,load_observer_table
from audit_v1 import evaluate,check
KIND='dq'
TASK='deb__dbt-dq-macro-enforcement'
RELATIONS={'dq':{'fct_order_line_detail':'main.fct_order_line_detail'},'reconciliation':{'stg_orders':'staging.stg_orders','stg_order_lines':'staging.stg_order_lines','stg_order_cancellations':'staging.stg_order_cancellations','int_orders_enriched':'intermediate.int_orders_enriched','mart_order_totals':'mart.mart_order_totals','mart_revenue_by_source':'mart.mart_revenue_by_source','mart_variance_details':'mart.mart_variance_details'}}
def checked(ref,root):
    p=root/ref['path'];b=p.read_bytes()
    if hashlib.sha256(b).hexdigest()!=ref['sha256']:raise CaptureError('Changed evidence '+str(p))
    return b
def recursive(x):
    if isinstance(x,dict):return decode(x)if'$type'in x else{k:recursive(v)for k,v in x.items()}
    if isinstance(x,list):return[recursive(v)for v in x]
    return x
def read_snapshot(spec,root):
    sr=spec['candidate_snapshot'];p=root/sr['path'];s=json.loads(checked(sr,root))
    if s.get('task_key')!=TASK or s.get('stage')!=spec['candidate_stage']:raise CaptureError('Wrong task/stage')
    outputs={};limits=[]
    for name,relation in RELATIONS[KIND].items():
        matches=[o for o in s['outputs']if o['specification']['relation']==relation and o['specification']['database']=='/app/database/retail.duckdb']
        if len(matches)!=1:raise CaptureError('Relation not uniquely captured '+relation)
        x=matches[0];out={};err=x.get('error')
        if err:
            if err.get('type')=='CatalogException'and('Table with name '+name+' does not exist'in err.get('message','')or'Schema with name '+relation.split('.')[0]+' does not exist'in err.get('message','')):out['exists']=False
        else:
            fn=x['file']
            if Path(fn).name!=fn:raise CaptureError('Nonlocal row file')
            data=checked({'path':str(p.parent/fn),'sha256':x['sha256']},root);cols=[c['name'].lower()for c in x['schema']];rows=[json.loads(l)for l in data.splitlines()]
            if len(cols)!=len(set(cols))or len(rows)!=x['rows']or any(len(r)!=len(cols)for r in rows):raise CaptureError('Malformed captured rows')
            out['rows']=[dict(zip(cols,map(decode,r)))for r in rows]
            try:out['table']=load_observer_table(sr,task_key=TASK,database='/app/database/retail.duckdb',relation=relation,stage=spec['candidate_stage'],root=root)
            except CaptureError as e:limits.append(dict(relation=relation,reason=str(e)))
        outputs[name]=out
    return outputs,limits
def staging(outputs,raw):
    # Basic staging is underspecified in projection; an unfamiliar legal shape is unknown.
    # Verify required source keys/financial fields when the conventional names are present.
    from collections import Counter
    from decimal import Decimal
    mappings={'stg_orders':('orders',('order_id','order_source','subtotal','grand_total','tax_total')),'stg_order_lines':('lines',('order_line_id','order_id','quantity_ordered','tax_amount','line_total')),'stg_order_cancellations':('cancellations',('cancellation_id','order_id'))}
    details={}
    for name,(key,cols)in mappings.items():
        o=outputs.get(name,{})
        if o.get('exists')is False:details[name]='fail: required staging relation absent';continue
        if o.get('rows')is None:details[name]='pending: staging rows unavailable';continue
        rows=o['rows']
        if any(not set(cols)<=set(r)for r in rows):details[name]='pending: alternative staging projection requires lineage review';continue
        def normalize(r):
            return tuple((r[c].strip()if isinstance(r[c],str)else r[c])if c.endswith('_id')or c=='order_source'else Decimal(0)if r[c]is None else Decimal(str(r[c]))for c in cols)
        want=Counter(map(normalize,raw[key]));got=Counter(map(normalize,rows));details[name]='pass'if want==got else'fail: source row multiplicity or required calculation fields differ'
    status='fail'if any(v.startswith('fail')for v in details.values())else'pending'if any(v.startswith('pending')for v in details.values())else'pass'
    return check(status,'Source-based staging comparison; no invented projection completeness requirement',details=details)
def run(spec,root):
    try:
        if spec.get('task_key')!=TASK:raise CaptureError('Wrong audit task')
        original=json.loads(checked(spec['original_input'],root))
        for r in original['source_refs']:checked(r,root)
        raw=recursive(original['rows']);workflow=None
        if spec.get('trusted_workflow'):
            from workflow_v1 import evaluate as wf
            workflow=wf(KIND,json.loads(checked(spec['trusted_workflow'],root)))
        snap=json.loads(checked(spec['candidate_snapshot'],root));captured={};local=[]
        for item in snap.get('files',[]):
            if item.get('exists')and'text'in item:
                if hashlib.sha256(item['text'].encode()).hexdigest()!=item['sha256']:raise CaptureError('Captured source hash mismatch')
                captured[item['path']]=item['text']
            elif item.get('exists')is False:captured[item['path']]=None
        project='/app/dbt_models_duckdb'
        mp=project+'/models/marts/sales/fct_order_line_detail.sql'
        if mp in captured and captured[mp]is None:local.append('Explicit required DQ model path is absent')
        pp=project+'/profiles.yml'
        if pp in captured and captured[pp]is None:local.append('Required local profile is explicitly absent')
        elif pp in captured:
            try:
                cfg=yaml.safe_load(captured[pp]);valid=isinstance(cfg,dict)and'retail_dw_master'in cfg
                if not valid:local.append('Required local retail_dw_master profile is absent')
            except (TypeError,yaml.YAMLError):local.append('Required local profile is absent or invalid YAML')
        if local:workflow=check('fail','; '.join(local))
        if KIND=='dq':
            from expected_v1 import full_derive,parse_patterns
            text=checked(original['initial_macro'],root).decode();patterns=parse_patterns(text)
            if len(patterns)!=4:raise CaptureError('Public initial PII patterns not recognized')
            expected,facts=full_derive(raw,patterns);outputs,limits=read_snapshot(spec,root)
            result=evaluate(outputs,expected,workflow=workflow,initial_rows=recursive(original['initial_rows']))
        else:
            from expected_v1 import build
            expected,facts=build(raw);outputs,limits=read_snapshot(spec,root);result=evaluate(outputs,expected,workflow=workflow,staging=staging(outputs,raw))
        result.update(task_key=TASK,statistics=facts,capture_limitations=limits,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v});return result
    except(OSError,ValueError,KeyError,TypeError,AssertionError,ArithmeticError,AttributeError)as e:return dict(task_key=TASK,status='unknown',reward=None,reason=str(e),error_type=type(e).__name__,model_requests=0,native_rewards_modified=False)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();result=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(result,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'audit_reward':result['reward']}))
