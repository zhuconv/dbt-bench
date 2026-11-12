"""Original-input affinity audit: flexible declared thresholds, exact associations."""
from pathlib import Path
from dataclasses import replace
from collections import Counter
from decimal import Decimal
import argparse,hashlib,importlib.util,json,sys
HERE=Path(__file__).parent;TASK='deb__dbt-harbor-product-affinity';DB='/app/database/retail.duckdb'
STAGE='stg_basket__order_products';PAIRS='int_affinity__product_pairs';RULES='int_affinity__association_rules';FACT='fct_product_affinity_matrix';BUNDLES='rpt_recommended_bundles'
KEYS={STAGE:(),PAIRS:('order_id','product_a_id','product_b_id'),RULES:('product_a_id','product_b_id'),FACT:('product_a_id','product_b_id'),BUNDLES:('bundle_rank',)}
def module(path):
 p=Path(path);s=importlib.util.spec_from_file_location('affinity_'+p.stem,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def contract(name,schema):
 from dbt_bench.comparison import Column,TableContract
 return TableContract(tuple(Column(n,'integer'if t=='INTEGER'else'numeric'if t=='DOUBLE'else'text',abs_tol=Decimal('1e-10')if t=='DOUBLE'else 0,rel_tol=Decimal('1e-12')if t=='DOUBLE'else 0)for n,t in schema),KEYS[name],allow_extra_columns=True)
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy
 from dbt_bench.comparison import compare_tables
 wf=module(HERE/'workflow.py');helper=module(HERE/'project_workflow.py');business=module(HERE/'business_policy.py');policy=json.loads((HERE/'policy.json').read_text());schemas=json.loads((HERE/'schemas.json').read_text())['relations'];inputs=json.loads(wf.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];wf.read_ref(policy['replay_engine'],root);replay=module(root/policy['replay_engine']['path']);expected,raw=module(HERE/'derive_expected.py').derive_harbor(replay.Replay(inputs));parts={};tables={};legacy=None;declared=None
 if request.get('legacy_case_proof'):
  legacy=json.loads(wf.read_ref(request['legacy_case_proof'],root));assert legacy['task_key']==TASK and legacy['raw_observations']['sources']==raw['sources']
 else:
  try:declared=business.inspect(json.loads(wf.read_ref(request['parsed_snapshot'],root)),helper);parts['declared_policy']=declared
  except Exception as e:parts['declared_policy']={'status':'unknown','reward':None,'reason':str(e)}
 for relation,wanted in expected.items():
  name=relation.split('.')[-1];ct=contract(name,schemas[relation])
  try:
   if legacy is not None:rows=legacy['after'][relation]['rows'];tables[name]=rows;parts['interface:'+name]={'status':'unknown','reward':None,'reason':'Historic value proof lacks complete current SQL interface/workflow certification'}
   else:
    t=load_observer_table(request['snapshot'],task_key=TASK,database=DB,relation=relation,stage=request['stage'],root=root);t=replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=[{n.lower():v for n,v in row.items()}for row in t.rows]);tables[name]=t.rows;parts['interface:'+name]=audit_table(t,TablePolicy(ct,{n:(typ,)for n,typ in schemas[relation]},not_null=ct.keys,relation_types=tuple(policy['models'][name]['physical_kinds'])))
  except Exception as e:parts['interface:'+name]={'status':'unknown','reward':None,'reason':str(e)}
 def scored(name,ok,reason,**more):parts[name]={'status':'scored','reward':float(ok),'reason':reason,**more}
 try:
  columns=[n for n,t in schemas['main_staging.'+STAGE]];key=lambda r:tuple(r[n]for n in columns);a=Counter(map(key,tables[STAGE]));w=Counter(map(key,expected['main_staging.'+STAGE]));valid=set(a)==set(w)and all(1<=n<=w[k]for k,n in a.items())
  if declared is not None and not declared['stage_policy_known']:parts['values:'+STAGE]={'status':'unknown','reward':None,'reason':'Executed submission has an unreviewed data-quality policy; not inferred from output or gold','policy':declared['unsupported_stage']}
  else:scored('values:'+STAGE,valid,'Original catalog-backed line identities and descriptions; legitimate duplicate elimination allowed',expected_distinct=len(w),actual_distinct=len(a))
 except Exception as e:parts['values:'+STAGE]={'status':'unknown','reward':None,'reason':str(e)}
 for name in(PAIRS,RULES):
  try:
   ct=contract(name,schemas['main_intermediate.'+name]);c=compare_tables(expected['main_intermediate.'+name],tables[name],ct)
   if declared is not None and not declared['stage_policy_known']:parts['values:'+name]={'status':'unknown','reward':None,'reason':'Unreviewed upstream data-quality policy needs independent derivation'}
   else:scored('values:'+name,c.equal,c.reason)
  except Exception as e:parts['values:'+name]={'status':'unknown','reward':None,'reason':str(e)}
 try:
  categories={r['product_id']:r['category_id']for r in expected['main_staging.'+STAGE]};index={(r['product_a_id'],r['product_b_id']):r for r in expected['main_intermediate.'+RULES]};wanted=[];all_known=True;fact=tables[FACT]
  for row in fact:
   k=(row['product_a_id'],row['product_b_id']);rule=index.get(k)
   if rule is None:all_known=False;continue
   wanted.append({n:(categories[k[0]]if n=='category_a_id'else categories[k[1]]if n=='category_b_id'else rule[n])for n,t in schemas['main_marts.'+FACT]})
  c=compare_tables(wanted,fact,contract(FACT,schemas['main_marts.'+FACT]));cross=all(r['category_a_id']!=r['category_b_id']for r in fact);positive=all(r['lift']>1 for r in fact);high=sum(r['lift']>1.5 for r in fact)
  predicates=declared['thresholds']if declared else {};limits=all(business.satisfies(r,predicates)for r in fact)
  if declared is not None and not declared['stage_policy_known']:parts['values:'+FACT]={'status':'unknown','reward':None,'reason':'Unreviewed upstream policy'}
  else:scored('values:'+FACT,all_known and c.equal and cross and positive and high>=100 and limits,c.reason,all_source_rules_known=all_known,cross_category=cross,all_positive_lift=positive,high_lift_rows=high,declared_thresholds_satisfied=limits)
  # Rank the independently verified fact. Fact failure remains a separate zero.
  ranked=sorted(fact,key=lambda r:(-r['lift'],-r['confidence'],-r['orders_with_both'],r['product_a_id'],r['product_b_id']))[:50];bundles=[{'bundle_rank':i+1,**{n:r[n]for n,t in schemas['main_marts.'+BUNDLES]if n!='bundle_rank'}}for i,r in enumerate(ranked)];c=compare_tables(bundles,tables[BUNDLES],contract(BUNDLES,schemas['main_marts.'+BUNDLES]));ordered=[r['bundle_rank']for r in tables[BUNDLES]]==list(range(1,51));scored('values:'+BUNDLES,c.equal and ordered,c.reason,required_order=ordered)
 except Exception as e:parts['fact_or_bundles']={'status':'unknown','reward':None,'reason':str(e)}
 if legacy is None:
  for relation in expected:
   schema,name=relation.split('.')
   try:parts['build:'+name]=wf.workflow_check(request['workflow'],request['snapshot'],TASK,root,name,schema)
   except Exception as e:parts['build:'+name]={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['project_workflow']=helper.evaluate(request,root,policy['models'])
  except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 else:parts['declared_policy']={'status':'unknown','reward':None,'reason':'Historic values do not certify actual source-declared thresholds'}
 scores=[p['reward']for p in parts.values()];reward=0.0 if 0.0 in scores else None if None in scores else 1.0;paths=[HERE/n for n in('check.py','derive_expected.py','schemas.json','policy.json','workflow.py','project_workflow.py','business_policy.py')]+[root/policy['replay_engine']['path'],root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
 return {'schema_version':1,'task_key':TASK,'target':'audit','status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'raw_observations':raw,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Original-input keyed pair/rule arithmetic; legitimate stage deduplication and source-declared threshold choices; fact association and minimum high-lift/cross-category rules; independently ranked actual verified fact; actual workflow. Unreviewed data-quality policy remains unknown.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
