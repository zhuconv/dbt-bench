"""Independent fixed-input audit with separate actual workflow evidence."""
from pathlib import Path
from collections import Counter
from dataclasses import replace
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
import argparse,hashlib,importlib.util,json,sys
HERE=Path(__file__).parent;TASK='deb__'+HERE.parent.name;KIND='inventory'if TASK.endswith('inventory-analysis')else'loyalty';SCHEMA='inventory_analytics'if KIND=='inventory'else'loyalty_analytics'
MODELS={'levels':'stg_inventory_levels','warehouses':'stg_warehouses','final':'fct_warehouse_inventory'}if KIND=='inventory'else{'transactions':'stg_lp_transactions','programs':'stg_lp_programs','customers':'stg_lp_customers','intermediate':'int_loyalty_metrics','final':'program_loyalty_summary'}
D=Decimal

def module(name):
 s=importlib.util.spec_from_file_location(TASK+'_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def normalized(t):return replace(t,columns=tuple(n.casefold()for n in t.columns),physical_columns=tuple(n.casefold()for n in t.physical_columns),sql_types={n.casefold():v for n,v in t.sql_types.items()},physical_sql_types={n.casefold():v for n,v in t.physical_sql_types.items()},rows=[{n.casefold():v for n,v in r.items()}for r in t.rows])
def numeric_sql(t):return t in('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT','FLOAT','DOUBLE')or t.startswith('DECIMAL(')
def finite(v):return type(v)in(int,float,D)and D(str(v)).is_finite()
def rate_compatible(actual,exact):
 if not finite(actual):return False
 value=D(str(actual))
 if value==exact or type(actual)is float and actual==float(exact):return True
 scale=max(0,-value.as_tuple().exponent)
 if scale>18:return False
 unit=D(1).scaleb(-scale)
 return value in{exact.quantize(unit,rounding=ROUND_FLOOR),exact.quantize(unit,rounding=ROUND_CEILING)}
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');derive=module('derive_expected');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root))
 if request['task_key']!=TASK or request.get('initial_database_sha256')!=inputs['initial_database_sha256']:raise ValueError('Task or original input binding mismatch')
 expected=derive.derive(inputs,decode);parts={};models=request.get('inventory_model_roles',MODELS)if KIND=='inventory'else MODELS
 if set(models)!=set(MODELS)or models['final']!=MODELS['final']:raise ValueError('Incomplete required model roles')
 for role,name in models.items():
  try:
   table=normalized(load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=SCHEMA+'.'+name,stage=request['stage'],root=root))
   if len(set(table.columns))!=len(table.columns):raise ValueError('Ambiguous column case')
   if role in expected['stages']:
    gold=expected['stages'][role];key={'levels':'inventory_id','warehouses':'warehouse_id','transactions':'transaction_id','programs':'program_id','customers':'customer_id'}[role];mapping=request.get('stage_column_maps',{}).get(role,{n:n for n in gold[0]if n in table.columns})
    if key not in mapping:raise ValueError('Staging aliases need a semantic key mapping; staging field layout is not prescribed')
    lookup={r[key]:r for r in gold}
    if len(lookup)!=len(gold):raise ValueError('Raw key is not unique; independent multimap review required')
    if not table.rows:raise ValueError('Empty stage has no observed normalization behavior')
    checks={'raw_associations':True,'exposed_values_cleaned':True}
    for row in table.rows:
     ident=row[mapping[key]]
     if ident not in lookup:checks['raw_associations']=False;continue
     for canonical,physical in mapping.items():
      if canonical not in lookup[ident]or physical not in row:raise ValueError('Unknown staging semantic mapping')
      a,b=row[physical],lookup[ident][canonical]
      equal=finite(a)and finite(b)and D(str(a))==D(str(b))if type(b)in(int,float,D)else type(a)is type(b)and a==b
      if not equal:checks['exposed_values_cleaned']=False
    parts[role]={'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Observed staged rows retain raw associations and explicit cleaning for exposed fields. Optional columns and early filtering are not fixed to reference. No counterfactual behavior inferred when original data has no NULL/whitespace.'}
   else:
    gold=expected['final']if role=='final'else expected['intermediate'];cols=tuple(gold[0]);observed=table
    if KIND=='inventory':
     mapping=request.get('inventory_final_columns',{n:n for n in cols})
     if set(mapping)!=set(cols)or len(set(mapping.values()))!=len(cols):raise ValueError('Incomplete warehouse semantic mapping')
     if 'avg_unit_cost'not in table.columns:parts[role]={'status':'scored','reward':0.0,'reason':'Explicit avg_unit_cost column missing'};raise StopIteration
     if not set(mapping.values())<=set(table.columns):raise ValueError('Warehouse field aliases require explicit semantic mapping; only avg_unit_cost is explicitly named by instruction')
     table=replace(table,columns=tuple(mapping),physical_columns=tuple(mapping),sql_types={n:table.sql_types[v]for n,v in mapping.items()},physical_sql_types={n:table.physical_sql_types[v]for n,v in mapping.items()},rows=[{n:r[v]for n,v in mapping.items()}for r in table.rows])
    text={n for n,v in gold[0].items()if isinstance(v,str)};columns=[];types={};integer_sql=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
    for n in cols:
     integer=KIND=='loyalty'and n=='avg_points_per_member';columns.append(Column(n,'text'if n in text else'integer'if integer else'numeric'));actual=table.physical_sql_types.get(n,'__missing__');types[n]=('VARCHAR',)if n in text else integer_sql if integer else(actual,)if numeric_sql(actual)else('__required_numeric__',)
    cooked=[dict(r)for r in table.rows];target=[dict(r)for r in gold]
    if KIND=='loyalty'and role=='final':
     exact=expected['exact'];target_byid={r['program_id']:r for r in target}
     for row in cooked:
      key=row.get('program_id')
      if key not in exact:continue
      target_byid[key]['avg_points_per_member']=exact[key]['integer_floor'];target_byid[key]['redemption_rate']=exact[key]['rate']
      if row.get('avg_points_per_member')in(exact[key]['integer_floor'],exact[key]['integer_ceil'])and type(row.get('avg_points_per_member'))is int:row['avg_points_per_member']=exact[key]['integer_floor']
      if rate_compatible(row.get('redemption_rate'),exact[key]['rate']):row['redemption_rate']=exact[key]['rate']
    key='warehouse_id'if KIND=='inventory'else'program_id';result=audit_table(replace(table,rows=cooked),TablePolicy(TableContract(tuple(columns),keys=(key,),allow_extra_columns=True),types,not_null=cols if role=='final'else(key,),relation_types=('BASE TABLE',)if role=='final'else('BASE TABLE','VIEW')),target)
    if role=='final':
     nonnegative=['total_quantity','inventory_value','distinct_product_count','avg_unit_cost','max_single_item_value']if KIND=='inventory'else['member_count','points_earned','points_redeemed','points_expired','points_balance','avg_points_per_member'];bounds='inventory_concentration'if KIND=='inventory'else'redemption_rate';domain=all(finite(r.get(bounds))and(0<r[bounds]<=1 if KIND=='inventory'else 0<=r[bounds]<=100)for r in table.rows);quality={'no_null_any_column':all(v is not None for r in observed.rows for v in r.values()),'nonnegative':all(finite(r.get(n))and r[n]>=0 for r in table.rows for n in nonnegative),'domain':domain};result['checks'].update(quality)
     if not all(quality.values()):result.update(status='scored',reward=0.0)
    parts[role]=result
  except StopIteration:pass
  except Exception as e:parts[role]={'status':'unknown','reward':None,'reason':str(e)}
  try:
   path='models/marts/'+name+'.sql'if role=='final'else'models/intermediate/'+name+'.sql'if role=='intermediate'else None;parts['model:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,name,SCHEMA,path,role=='final')
  except Exception as e:parts['model:'+role]={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow').evaluate(request,root,models,KIND)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 if KIND=='loyalty':parts['remaining_policy']={'status':'unknown','reward':None,'reason':'Actual raw mean is noninteger and percentage is nonterminating. Nearest/truncated/upward representations consistent with the actual reported precision are retained only as scoped possibilities, never full lawful acceptance. No global numeric tolerance or reference-specific rounding is imposed.'}
 rewards=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0;paths=[HERE/n for n in('check.py','workflow.py','project_workflow.py','derive_expected.py','policy.json')]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
 return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Independent original input associations, definite arithmetic/structure and actual trusted workflow. Inventory aliases/optional staging representation allowed; Loyalty unresolved number presentation stays explicit.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:result=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:result={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
