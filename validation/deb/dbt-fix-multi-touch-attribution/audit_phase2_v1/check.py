"""Independent current-original-input Multi-touch audit with trusted workflow."""
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
import argparse,hashlib,importlib.util,json,re,sys
HERE=Path(__file__).parent;TASK='deb__dbt-fix-multi-touch-attribution';MODEL='rpt_multi_touch_attribution_fixed';RELATION='analytics.'+MODEL

def module(name):s=importlib.util.spec_from_file_location('multitouch_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');inputs=json.loads(workflow.read_ref(json.loads((HERE/'policy.json').read_text())['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];derive=module('derive_expected');expected,proof=derive.derive(inputs,decode);parts={}
 try:
  t=load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=RELATION,stage=request['stage'],root=root);t=replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=[{n.lower():v for n,v in r.items()}for r in t.rows]);actual={tuple(r.get(k)for k in derive.KEY):r for r in t.rows}
  for row in expected:
   k=tuple(row[n]for n in derive.KEY);value=actual.get(k,{}).get('conversion_rate')
   if value in proof['ratio_options'][k]:row['conversion_rate']=value
  ints={'sessions','conversions','assist_sessions'};cols=[Column(n,'date'if n=='attribution_date'else'integer'if n in ints else'numeric'if n in ('attributed_revenue','conversion_rate')else'text')for n in expected[0]];sql={n:('DATE',)if n=='attribution_date'else('INTEGER',)if n in ints else('DECIMAL(12,2)',)if n=='attributed_revenue'else(t.sql_types.get(n),)if n=='conversion_rate'and re.fullmatch(r'DECIMAL\(\d+,\d+\)',t.sql_types.get(n,''))else('DECIMAL(10,4)',)if n=='conversion_rate'else('VARCHAR',)for n in expected[0]};parts['table']=audit_table(t,TablePolicy(TableContract(tuple(cols),keys=derive.KEY,allow_extra_columns=True),sql),expected)
 except Exception as e:parts['table']={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['workflow']=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,MODEL,'analytics','models/marts/marketing/'+MODEL+'.sql')
 except Exception as e:parts['workflow']={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow').evaluate(request,root,inputs)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 scores=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in scores else None if None in scores else 1.0;paths=[HERE/n for n in ['check.py','derive_expected.py','workflow.py','project_workflow.py','policy.json']]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'original_input_conditions':proof['conditions'],'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'All11450 rows/11fields of the actual original single-session/zero-purchase input, exact specified types and keys, nearest four-decimal rates, plus trusted required workflow. Conditional nonzero-purchase/source-selection and multi-session branches are not certified by this data.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
