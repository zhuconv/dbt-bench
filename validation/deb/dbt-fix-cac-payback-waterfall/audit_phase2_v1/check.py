"""Independent CAC audit with all maximum-spend tied channels admissible."""
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
from collections import defaultdict
import argparse,hashlib,importlib.util,json,sys
HERE=Path(__file__).parent;TASK='deb__dbt-fix-cac-payback-waterfall';MODEL='rpt_cac_payback_waterfall_fixed';RELATION='analytics.'+MODEL;MODEL_PATH='models/marts/marketing/'+MODEL+'.sql'
def module(name):s=importlib.util.spec_from_file_location('cac_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def normalize(t):return replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=[{n.lower():v for n,v in r.items()}for r in t.rows])
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];gold,details=module('derive_expected').derive(inputs,decode);parts={}
 try:
  t=normalize(load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=RELATION,stage=request['stage'],root=root));actual={(r.get('campaign_id'),r.get('days_since_cohort')):r for r in t.rows};channels=defaultdict(set)
  for r in t.rows:channels[r.get('campaign_id')].add(r.get('channel_key'))
  for r in gold:
   campaign=r['campaign_id'];key=campaign,r['days_since_cohort'];reported=actual.get(key,{})
   if len(channels[campaign])==1 and next(iter(channels[campaign]))in details['allowed_channels'][campaign]:r['channel_key']=next(iter(channels[campaign]))
   value=reported.get('payback_ratio')
   if type(value)in(int,float,Decimal)and Decimal(str(value)).is_finite()and Decimal(str(value))in details['ratio_options'][key]:r['payback_ratio']=Decimal(str(value))
  kinds={'campaign_id':'text','channel_key':'integer','cohort_date':'date','days_since_cohort':'integer','is_paid_back':'boolean'};types={n:('VARCHAR',)if n=='campaign_id'else('INTEGER',)if n in('channel_key','days_since_cohort')else('DATE',)if n=='cohort_date'else('BOOLEAN',)if n=='is_paid_back'else('DECIMAL(10,4)',)if n=='payback_ratio'else('DECIMAL(12,2)',)for n in gold[0]};contract=TableContract(tuple(Column(n,kinds.get(n,'numeric'))for n in gold[0]),keys=('campaign_id','days_since_cohort'),allow_extra_columns=True);parts['table']=audit_table(t,TablePolicy(contract,types,not_null=tuple(gold[0])),gold);parts['one_channel']={'status':'scored','reward':float(all(len(v)==1 for v in channels.values())),'scope':'One selected channel across all days, including campaigns with source ties.'}
 except Exception as e:
  parts['table']={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['projected_channel_error']=module('projected_channel_error').inspect(request,root,details['allowed_channels'])
  except Exception as q:parts['projected_channel_error']={'status':'unknown','reward':None,'reason':str(q)}
 try:parts['model_workflow']=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,MODEL,'analytics',MODEL_PATH)
 except Exception as e:parts['model_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow').evaluate(request,root)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 rewards=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0;paths=[HERE/n for n in ['check.py','projected_channel_error.py','derive_expected.py','workflow.py','project_workflow.py','policy.json']]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'source_conditions':{'rows':details['rows'],'campaigns':details['campaigns'],'minimum_cumulative_spend':str(details['minimum_cumulative_spend']),'tied_max_channels':details['channel_ties']},'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'All ten fields from original spend inputs, declared exact SQL types and campaign/day grain plus trusted actual delivery/workflow. Highest-spend channel ties are allowed; no extra output order, SQL implementation or profile environment requirement.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:result=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:result={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
