"""Trust-risk audit: independently derived current inputs and captured dbt workflow."""
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
import argparse,hashlib,importlib.util,json,re,sys
HERE=Path(__file__).parent
TASK='deb__dbt-retail-task-10-trust-risk'
DB='/app/database/retail.duckdb'
INT_TYPES=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')

def module(name):
 s=importlib.util.spec_from_file_location('trust_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def contract(model,schemas,policy):
 from dbt_bench.comparison import Column,TableContract
 kinds={'string':'text','timestamp':'datetime'}
 columns=[]
 for n in schemas['columns'][model]:
  p=policy['numeric_policy'].get(model,{}).get(n,{})
  columns.append(Column(n,kinds.get(schemas['types'][model][n],schemas['types'][model][n]),abs_tol=p.get('abs_tol','0'),rel_tol=p.get('rel_tol','0')))
 return TableContract(tuple(columns),() if model=='rpt_review_moderation_kpis' else ('review_id',))

def normalize(rows,model,schemas):
 result=[];bad=[]
 for i,original in enumerate(rows):
  row={k.lower():v for k,v in original.items()}
  if len(row)!=len(original):bad.append((i,'casefold_collision'))
  for name,kind in schemas['types'][model].items():
   v=row.get(name)
   if kind!='integer' or v is None:continue
   if type(v)not in(int,float,Decimal)or not Decimal(str(v)).is_finite()or Decimal(str(v))!=Decimal(str(v)).to_integral_value():bad.append((i,name))
   else:row[name]=int(v)
  result.append(row)
 return result,bad

def canonical_empty_groups(rows,expected,model):
 if model=='rpt_review_moderation_kpis':return rows
 bykey={r['review_id']:r for r in expected};groups={'return_count':('return_count','total_refund_amount'),'product_return_line_count':('product_return_line_count','product_return_qty','product_return_refund'),'coupon_redemption_count':('coupon_redemption_count','coupon_discount_total'),'product_event_7d_count':('product_event_7d_count',)};out=[]
 for original in rows:
  row=dict(original);e=bykey.get(row.get('review_id'))
  if e is not None:
   for marker,columns in groups.items():
    if e[marker] is not None:continue
    for name in columns:
     value=row.get(name)
     if e[name] is None and type(value)in(int,float,Decimal)and value==0:row[name]=None
  out.append(row)
 return out

def legacy_rows(request,model,root,read,decode):
 if request.get('legacy_case_output'):
  proof=json.loads(read(request['legacy_case_proof'],root));assert proof['task_key']==TASK and proof['observed_output']['sha256']==request['legacy_case_output']['sha256'];captured=json.loads(read(request['legacy_case_output'],root))[model];legacy_decode=module('derive_expected').decode;return [{k:legacy_decode(v)for k,v in row.items()}for row in captured['rows']],captured['columns']
 p=root/request['legacy_value_snapshot']['path'];snap=json.loads(read(request['legacy_value_snapshot'],root));assert snap['task_key']==TASK and snap['stage']==request['stage'];items=[i for i in snap['outputs']if i['specification']['relation']=='main.'+model and i['specification']['database']==DB];assert len(items)==1;i=items[0];assert not i.get('error');name=i['file'];assert Path(name).name==name and not(p.parent/name).is_symlink();raw=(p.parent/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==i['sha256'];cols=[c['name']for c in i['schema']];rows=[]
 for line in raw.splitlines():
  values=json.loads(line);assert len(values)==len(cols);rows.append(dict(zip(cols,map(decode,values))))
 assert len(rows)==i['rows'];return rows,cols

def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import compare_tables
 wf=module('workflow_v2');derive=module('derive_expected');schemas=json.loads((HERE/'schemas.json').read_text());policy=json.loads((HERE/'policy_v2.json').read_text());inputs=json.loads(wf.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];raw={'tables':{n.removeprefix('main.'):{'rows':[dict(zip([c['name']for c in v['schema']],row))for row in v['rows']]}for n,v in inputs['relations'].items()}};derived=derive.derive(raw,schemas['columns']);assert not derived['unresolved'];parts={}
 for model,rows in derived['outputs'].items():
  tc=contract(model,schemas,policy);rows,bad=normalize(rows,model,schemas);assert not bad
  try:
   if request.get('legacy_value_snapshot')or request.get('legacy_case_output'):
    prediction,columns=legacy_rows(request,model,root,wf.read_ref,decode);prediction,invalid=normalize(prediction,model,schemas);prediction=canonical_empty_groups(prediction,rows,model);comparison=compare_tables(rows,prediction,tc);parts[model]={'status':'scored','reward':float(not invalid and comparison.equal and [n.lower()for n in columns]==schemas['columns'][model]),'reason':comparison.reason,'invalid_numeric':invalid[:5],'scope':'Historic hash-bound value projection and declared column order; SQL types/workflow unknown separately.'};continue
   t=load_observer_table(request['snapshot'],task_key=TASK,database=DB,relation='main.'+model,stage=request['stage'],root=root);prediction,invalid=normalize(t.rows,model,schemas);prediction=canonical_empty_groups(prediction,rows,model);t=replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=prediction);types={}
   for c in tc.columns:
    typ=t.sql_types.get(c.name,'');numeric=bool(re.fullmatch(r'(U?TINYINT|U?SMALLINT|U?INTEGER|U?BIGINT|U?HUGEINT|FLOAT|DOUBLE|DECIMAL\(\d+,\d+\))',typ));types[c.name]=(typ,)if c.kind=='numeric'and numeric else('DOUBLE',)if c.kind=='numeric'else INT_TYPES if c.kind=='integer'else('TIMESTAMP','TIMESTAMP_MS','TIMESTAMP_NS','TIMESTAMP_S')if c.kind=='datetime'else('DATE',)if c.kind=='date'else('BOOLEAN',)if c.kind=='boolean'else('VARCHAR',)
   parts[model]=audit_table(t,TablePolicy(tc,types,not_null=tc.keys,relation_types=('VIEW',)),expected_rows=rows)
   physical_order=list(t.physical_columns)==schemas['columns'][model]
   parts[model]['physical_column_order']=physical_order
   if invalid or not physical_order:parts[model].update(status='scored',reward=0.0,reason='Declared integer value or physical column order violated',invalid_numeric=invalid[:5])
  except Exception as e:parts[model]={'status':'unknown','reward':None,'reason':str(e)}
 if request.get('legacy_value_snapshot')or request.get('legacy_case_output'):
  parts['workflow']={'status':'unknown','reward':None,'reason':'Historical value-only snapshot lacks trusted reconstructed source and SQL type evidence.'}
 else:
  for model in derived['outputs']:
   try:parts['build:'+model]=wf.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,'main')
   except Exception as e:parts['build:'+model]={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['project_workflow']=module('project_workflow_v2').evaluate(request,root)
  except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 scores=[p['reward']for p in parts.values()];reward=0.0 if 0.0 in scores else None if None in scores else 1.0;paths=[HERE/n for n in('check_v4.py','derive_expected.py','policy_v2.json','schemas.json','workflow_v2.py','project_workflow_v2.py')]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
 return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'independent_profile':{k:v for k,v in derived.items()if k!='outputs'},'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Three declared views,987/987/331 current-input rows and196 declared fields; full raw-input derivation, physical column order/type families and prescribed source/build forms. No gold or native reward used as expected; only independently absent aggregate groups acceptNULL/numeric0 equivalence. Existing all-NULLgroups and source dimensionNULLs remain distinct; inactive ambiguous branches not certified.'}

def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
