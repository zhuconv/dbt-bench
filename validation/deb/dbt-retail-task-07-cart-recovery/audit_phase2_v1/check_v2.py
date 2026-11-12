"""Cart recovery audit on full original inputs and trusted actual dbt builds."""
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
from datetime import datetime
import argparse,hashlib,importlib.util,json,re,sys
HERE=Path(__file__).parent;TASK='deb__dbt-retail-task-07-cart-recovery';DB='/app/database/retail.duckdb'
INTS=set('cart_item_count_reported item_line_count distinct_variants total_quantity duration_seconds page_views event_count checkout_event_count payment_error_count add_to_cart_count remove_from_cart_count product_view_count checkout_depth_score cart_add_net cart_line_count total_qty line_out_of_stock line_low_stock_status total_orders days_since_last_order customer_tenure_days tier_level email_consent sms_consent push_consent personalization_consent priority_score recovery_window_hours'.split())
NUMS=set('cart_subtotal_reported items_total cart_merch_value hours_since_last_activity cart_age_hours checkout_event_ratio min_quantity_available total_quantity_available total_lifetime_value churn_risk_score propensity_to_buy discount_percentage recovered_grand_total'.split())
BOOLS=set('is_converted is_abandoned inventory_risk_flag free_shipping incentive_flag is_recovered'.split());TIMES=set('created_at updated_at converted_at abandoned_loaded_at first_item_added_at last_item_added_at last_item_updated_at last_activity_at session_start session_end first_event_at last_event_at last_consent_at recovery_window_start recovery_window_end recovered_at scored_at'.split());CONSENT={n+'_consent'for n in('email','sms','push','personalization')}
INT_TYPES=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def module(name):
 s=importlib.util.spec_from_file_location('cart_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def normalized_rows(rows):
 out=[];bad=[]
 for i,original in enumerate(rows):
  row={n.lower():v for n,v in original.items()}
  if len(row)!=len(original):bad.append((i,'casefold_collision'))
  for name in INTS:
   v=row.get(name)
   if v is None:continue
   if type(v)not in(int,float,Decimal)or not Decimal(str(v)).is_finite()or Decimal(str(v))!=Decimal(str(v)).to_integral_value():bad.append((i,name))
   else:row[name]=int(v)
  out.append(row)
 return out,bad

def contract(model,derive,policy):
 from dbt_bench.comparison import Column,TableContract
 cols=[]
 for n in derive.COLUMNS[model]:
  kind='integer'if n in INTS else'numeric'if n in NUMS else'boolean'if n in BOOLS else'datetime'if n in TIMES else'text';p=policy['numeric_policy'].get(n,{});cols.append(Column(n,kind,abs_tol=p.get('abs_tol','0'),rel_tol=p.get('rel_tol','0')))
 key='session_id'if model.endswith('session_signals')else'customer_id'if model.endswith('customer_context')else'cart_id'
 return TableContract(tuple(cols),(key,),allow_extra_columns=True)

def legacy_rows(request,model,root,read,decode):
 p=root/request['legacy_value_snapshot']['path'];snap=json.loads(read(request['legacy_value_snapshot'],root));assert snap['task_key']==TASK and snap['stage']==request['stage'];items=[i for i in snap['outputs']if i['specification']['relation']=='main.'+model and i['specification']['database']==DB];assert len(items)==1;i=items[0];assert not i.get('error');name=i['file'];assert Path(name).name==name and not(p.parent/name).is_symlink();raw=(p.parent/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==i['sha256'];cols=[c['name']for c in i['schema']];rows=[]
 for line in raw.splitlines():
  values=json.loads(line);assert len(values)==len(cols);rows.append(dict(zip(cols,map(decode,values))))
 assert len(rows)==i['rows'];return rows

def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import compare_tables
 wf=module('workflow');derive=module('derive_expected');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(wf.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];derived=derive.derive_capture(inputs,decode);expected={m:[dict(zip(derive.COLUMNS[m],r))for r in rows]for m,rows in derived['expected'].items()};profile=derived['profile'];raw=derived['raw'];parts={}
 for model,rows in expected.items():
  tc=contract(model,derive,policy);rows,bad=normalized_rows(rows);assert not bad
  try:
   if request.get('legacy_value_snapshot'):
    prediction,invalid=normalized_rows(legacy_rows(request,model,root,wf.read_ref,decode));comparison=compare_tables(rows,prediction,tc);parts[model]={'status':'scored','reward':float(not invalid and comparison.equal),'reason':comparison.reason,'invalid_numeric':invalid[:5],'scope':'Only hash-bound historic value projection. Missing SQL types/workflow remain unknown separately.'};continue
   t=load_observer_table(request['snapshot'],task_key=TASK,database=DB,relation='main.'+model,stage=request['stage'],root=root);prediction,invalid=normalized_rows(t.rows);t=replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=prediction);types={}
   for c in tc.columns:
    typ=t.sql_types.get(c.name,'');numeric=bool(re.fullmatch(r'(U?TINYINT|U?SMALLINT|U?INTEGER|U?BIGINT|U?HUGEINT|FLOAT|DOUBLE|DECIMAL\(\d+,\d+\))',typ));types[c.name]=INT_TYPES if c.name in CONSENT else(typ,)if c.kind in('numeric','integer')and numeric else('DOUBLE',)if c.kind=='numeric'else INT_TYPES if c.kind=='integer'else('TIMESTAMP','TIMESTAMP_MS','TIMESTAMP_NS','TIMESTAMP_S')if c.kind=='datetime'else('BOOLEAN',)if c.kind=='boolean'else('VARCHAR',)
   parts[model]=audit_table(t,TablePolicy(tc,types,not_null=tc.keys,relation_types=('BASE TABLE',)if model=='fct_cart_recovery_priority'else('VIEW',)),expected_rows=rows)
   if invalid:parts[model]={'status':'scored','reward':0.0,'reason':'Nonfinite/nonintegral declared integer field','invalid_numeric':invalid[:5]}
  except Exception as e:parts[model]={'status':'unknown','reward':None,'reason':str(e)}
 if request.get('legacy_value_snapshot'):
  parts['workflow']={'status':'unknown','reward':None,'reason':'Historical value-only snapshot has no new trusted reconstruction workflow/SQL type evidence.'}
 else:
  for model in expected:
   try:parts['build:'+model]=wf.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,'main')
   except Exception as e:parts['build:'+model]={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['project_workflow']=module('project_workflow').evaluate(request,root)
  except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
  try:
   repeated=request['repeat_execution'];snapshots=[json.loads(wf.read_ref(repeated[k],root))for k in('first_snapshot','second_snapshot')];details={}
   for model in expected:
    ts=[load_observer_table(repeated[k],task_key=TASK,database=DB,relation='main.'+model,stage=s['stage'],root=root)for k,s in zip(('first_snapshot','second_snapshot'),snapshots)];left,right=[normalized_rows(t.rows)for t in ts];ct=contract(model,derive,policy);ct=replace(ct,columns=tuple(replace(c,abs_tol=Decimal(0),rel_tol=Decimal(0))for c in ct.columns));comparison=compare_tables(left[0],right[0],ct);details[model]={'equal':not left[1]and not right[1]and comparison.equal,'reason':comparison.reason}
   parts['idempotency']={'status':'scored','reward':float(all(v['equal']for v in details.values())),'relations':details,'scope':'Two actually executed dbt runs; complete declared values compared with exact zero numeric tolerance, unordered keys, unchanged necessary staging input bags. Includes intermediary values.'}
  except Exception as e:parts['idempotency']={'status':'unknown','reward':None,'reason':str(e)}
 scores=[p['reward']for p in parts.values()];reward=0.0 if 0.0 in scores else None if None in scores else 1.0;paths=[HERE/n for n in('check_v2.py','derive_expected.py','policy.json','workflow.py','project_workflow.py')]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'independent_profile':profile,'raw_input_projections':raw,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Full original input projections, five required output relations and129columns; exact keys, multiplicity, declared nullable joins, fixed clock and bounded ratio/hour tolerances only. Source/config/real build and explicit SQL forms independently checked; no native reward or gold value used to derive expected.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
