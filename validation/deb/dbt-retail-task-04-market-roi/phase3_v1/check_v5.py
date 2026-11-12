"""Original-input ROI audit: independent raw calculation and trusted run/test."""
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
import argparse,hashlib,importlib.util,json,math,re,sys
HERE=Path(__file__).parent;TASK='deb__dbt-retail-task-04-market-roi';MODEL='rpt_channel_roi_payback_monthly';RELATION='main.'+MODEL
INPUT_NAMES={'main.stg_fact_marketing_spend':'marketing','main.stg_analytics__dim_date':'dates','main.stg_analytics__fact_sales':'sales','main.stg_analytics__dim_customer':'customers','main.stg_analytics__dim_channel':'channels'}
def module(name):s=importlib.util.spec_from_file_location('roi_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def inputs_from_capture(capture,decode):return {INPUT_NAMES[name]:[dict(zip([v['name'].lower()for v in table['schema']],map(decode,row)))for row in table['rows']]for name,table in capture['relations'].items()}
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];derive=module('derive_expected');expected,proof=derive.recompute(inputs_from_capture(inputs,decode));parts={}
 if request.get('legacy_value_snapshot'):
  parts['interface']={'status':'unknown','reward':None,'reason':'Historical projected capture has no SQL DESCRIBE/physical schema'}
  try:parts['values']=module('legacy_values').compare(request,root,derive,expected,decode)
  except Exception as e:parts['values']={'status':'unknown','reward':None,'reason':str(e)}
 else:
  try:
   t=load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=RELATION,stage=request['stage'],root=root);t=replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=[{n.lower():v for n,v in r.items()}for r in t.rows]);rows=[];invalid=[]
   for i,original in enumerate(t.rows):
    row=dict(original)
    if 'month_start'in row:
     try:row['month_start']=derive.day(row['month_start'])
     except Exception:invalid.append((i,'month_start'))
    for name in derive.INTS:
     value=row.get(name)
     if value is not None:
      if type(value)not in(int,float,Decimal)or not Decimal(str(value)).is_finite()or Decimal(str(value))!=Decimal(str(value)).to_integral_value():invalid.append((i,name))
      else:row[name]=int(value)
    rows.append(row)
   t=replace(t,rows=rows);cols=[Column(n,'date'if n=='month_start'else'integer'if n in derive.INTS else'text'if n in derive.TEXT else'numeric')for n in sorted(derive.COLUMNS)];types={}
   for n in derive.COLUMNS:
    typ=t.sql_types.get(n,'');numeric=bool(re.fullmatch(r'(U?TINYINT|U?SMALLINT|U?INTEGER|U?BIGINT|U?HUGEINT|FLOAT|DOUBLE|DECIMAL\(\d+,\d+\))',typ));types[n]=('DATE','TIMESTAMP')if n=='month_start'else('VARCHAR',)if n in derive.TEXT else(typ,)if numeric else('INTEGER',)if n in derive.INTS else('DOUBLE',)
   structure=audit_table(t,TablePolicy(TableContract(tuple(cols),allow_extra_columns=True),types,not_null=('month_start','channel_key','impressions','clicks','conversions','spend_amount')));parts['interface']=structure
   if invalid:parts['values']={'status':'scored','reward':0.0,'reason':'Nonintegral/nonfinite count or non-date month','examples':invalid[:6]}
   elif structure['reward']==1:
    compared=derive.compare_output(expected,rows);parts['values']={'status':'scored','reward':float(compared['equal']),'comparison':compared,'numeric_policy':policy['numeric_policy'],'nullable_key':'Exact task-specific composite key; NULL segment/tier are legitimate and unique. No sentinel collision or numeric key tolerance.'}
  except Exception as e:parts['table']={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['workflow']=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,MODEL,'main','models/marts/marketing/'+MODEL+'.sql')
 except Exception as e:parts['workflow']={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow_v4').evaluate(request,root)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 scores=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in scores else None if None in scores else 1.0;paths=[HERE/n for n in ['check_v5.py','legacy_values.py','derive_expected.py','workflow.py','project_workflow_v4.py','portable_dateadd.py','policy.json']]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'independent_input_summary':proof,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Complete current original five staging inputs and790x25 required output values with exact nullable key/integer semantics and retained documented per-column numeric contract, plus trusted actual run/test/source/YAML/profile/backend workflow. No value proof from native score or SQL hash whitelist.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
