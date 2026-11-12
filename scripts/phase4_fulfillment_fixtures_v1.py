"""Execute one independently specified current-input counterexample per rule group."""
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import sys,json
from dbt_bench import io
from phase4_custom_probe_v4 import B,ROOT,verify,capture,Capture,outputs,runtime
from phase4_custom_final_v2 import translate,policy
from phase4_customer4_review_v3 import write,ref
from review_support_v3 import workflow as generic_workflow
C=ROOT/'validation/deb/dbt-fulfillment-sla/phase4_v1';sys.path.insert(0,str(C))
import expected_v5 as expected_module
import audit_v1 as audit
import workflow_v1 as form
TASK='dbt-fulfillment-sla';KEY='deb__'+TASK;P=B/'phase4-review-v1/pipeline/custom-v1'/TASK
FIELD={
 'transit-duration':('int_shipment_times','transit_days'),'shipment-counts':('fulfillment_sla','total_shipments'),'tier-shipment-counts':('fulfillment_sla','budget_shipments'),
 'warehouse-efficiency-score':('warehouse_performance','efficiency_score'),'warehouse-delay-flag':('int_shipment_times','warehouse_delay'),'warehouse-consistency-score':('warehouse_performance','consistency_score'),
 'previous-observed-month-growth':('fulfillment_sla','month_over_month_volume_change_pct'),'primary-carrier-share':('warehouse_performance','primary_carrier_pct'),'summary-service-tier':('fulfillment_summary','service_tier'),
 'tier-sla-rates':('fulfillment_sla','premium_sla_rate'),'primary-carrier-selection':('warehouse_performance','primary_carrier_name'),'late-only-mean':('fulfillment_sla','avg_days_late'),'interval-standard-deviation':('carrier_performance','transit_time_std_dev'),
 'express-partition-sla':('carrier_performance','express_sla_rate'),'delay-percentages':('fulfillment_sla','warehouse_delay_pct'),'continuous-percentiles':('fulfillment_sla','p75_total_days'),'summary-global-volume-share':('fulfillment_summary','pct_of_total_volume'),
 'carrier-via-shipping-method':('int_shipment_times','carrier_name'),'source-dimensions-and-associations':('fulfillment_sla','warehouse_name'),'active-month-count':('carrier_performance','active_months'),'carrier-composite-score':('carrier_performance','composite_score'),
 'sla-classification':('int_shipment_times','on_time_status'),'first-last-shipment-dates':('carrier_performance','first_shipment_date'),'mean-fulfillment-times':('fulfillment_sla','avg_total_days'),'carrier-consistency-score':('carrier_performance','consistency_score'),
 'within-type-performance-rank':('carrier_performance','rank_within_type'),'order-value-sums':('fulfillment_sla','total_order_value'),'overall-performance-rank':('carrier_performance','carrier_rank'),'integer-flag-representation':('int_shipment_times','is_express'),
 'sla-class-percentages':('fulfillment_sla','early_pct'),'carrier-delay-flag':('int_shipment_times','carrier_delay'),'breach-counts':('fulfillment_sla','severe_breach_count'),'delay-attribution':('int_shipment_times','delay_attribution'),
 'recent-sla-trend':('carrier_performance','recent_trend'),'warehouse-composite-score':('warehouse_performance','composite_score'),'order-value-tiers':('int_shipment_times','value_tier'),'mean-shipment-value':('carrier_performance','avg_shipment_value'),
 'distinct-orders':('carrier_performance','unique_orders'),'volume-weighted-score':('warehouse_performance','volume_weighted_score'),'minimum-maximum-time':('fulfillment_sla','max_total_days'),'delay-counts':('fulfillment_sla','both_delay_count'),
 'performance-tier':('carrier_performance','performance_tier'),'reliability-score':('carrier_performance','reliability_score'),'distinct-carrier-count':('warehouse_performance','distinct_carriers'),'holiday-peak-month':('fulfillment_sla','is_peak_month'),
 'carrier-speed-score':('carrier_performance','speed_score'),'express-partition-counts':('carrier_performance','standard_shipping_shipments'),'required-rounding':('fulfillment_sla','total_order_value'),'summary-carrier-volume-share':('fulfillment_summary','pct_of_carrier_volume'),
 'premium-budget-rate-difference':('carrier_performance','premium_to_budget_sla_diff'),'summary-value-distribution':('fulfillment_summary','value_tier_distribution'),'sla-class-counts':('fulfillment_sla','late_count'),
 'late-breach-severity':('int_shipment_times','breach_severity'),'sla-compliance-rate':('fulfillment_sla','sla_compliance_rate'),'null-method-name':('fulfillment_sla','shipping_method_name')}
def main():
 out=P/'fixtures-v1';assert not out.exists();out.mkdir();fresh=io.read_json(P/'fresh-v2/fresh-review.json');batch=Path(fresh['batch']['path']).parent;trial=batch/'jobs'/batch.name/fresh['records'][0]['trial'];v=verify(batch,trial);z=translate(v);_,raw,refs=capture(v,'initial');fc,_,_=capture(v,'final');expected,facts=expected_module.build_variants(raw);sr,original=outputs(z,'agent-submission');compiled={}
 for name,c in fc['compiled'].items():compiled[name]=[dict(zip(c['columns'],map(__import__('audit_tables').decode,json.loads(l))))for l in runtime(trial,c).read_text().splitlines()]
 parsed=io.read_json(P/'parser-v1/result.json');pr=next(r for r in parsed['records']if r['manifest']['path']==str(v['commands'][-1]['manifest_path']));form_result=form.inspect(pr['nodes'],v['commands'][-1]['manifest'],original);assert form_result['status']=='pass';assert generic_workflow(z,policy(TASK))['reward']==1
 wf={'status':'pass','reason':'Actual current process and parsed form checked','details':form_result};assert audit.evaluate('fulfillment',original,expected,workflow=wf,compiled=compiled)['reward']==1
 old=io.read_json(B/'phase2-review-v1/deb_operations/fraud-fulfillment-v1/fulfillment-audit-review-v1.json');ids=list(old['requirement_coverage'])+['BR-core-null-method-name'];records=[]
 def score(o,w=wf,cs=compiled):return audit.evaluate('fulfillment',o,expected,workflow=w,compiled=cs)
 for rid in ids:
  name=rid.removeprefix('BR-core-');o=deepcopy(original);w=deepcopy(wf);cs=deepcopy(compiled);changes=[];proof={'requirement':rid,'scope':'Explicit fixture on complete captured current positive; all allowed output baselines were recomputed from raw inputs before this independent change. No tested-checker result defines whether the case is wrong.','original_snapshot':sr,'raw':refs}
  if name in FIELD:
   rel,col=FIELD[name];row=next((r for r in o[rel]['rows']if r.get(col)is not None),o[rel]['rows'][0]);before=row.get(col);key={k:row[k]for k in expected_module.KEYS[rel]};allowed=[r[col]for ev in expected for r in ev[rel]if all(r[k]==value for k,value in key.items())];after=(str(before)+'_WRONG')if isinstance(before,str)else before+__import__('datetime').timedelta(days=1)if isinstance(before,__import__('datetime').date)else Decimal('0.5')if name=='integer-flag-representation'else Decimal(str(before))+Decimal('.001')if name=='required-rounding'else None if name=='null-method-name'else Decimal(str(before or 0))+1000
   assert all(after!=a for a in allowed);row[col]=after;o[rel]['table']=replace(o[rel]['table'],rows=o[rel]['rows']);proof.update(relation=rel,key=key,column=col,before=before,after=after,independent_allowed_values=list({str(a)for a in allowed}));changes.append({'relation':rel,'key':key,'column':col,'value':after})
  elif name in ('eligible-orders','eligible-shipment-joins'):
   rel='int_shipment_times';removed=o[rel]['rows'].pop();o[rel]['table']=replace(o[rel]['table'],rows=o[rel]['rows']);proof.update(removed_required_raw_shipment=removed['shipment_id'],independent_expected_population=len(expected[0][rel]));changes.append({'remove':removed})
  elif name=='shipment-and-output-grains':
   rel='fulfillment_sla';o[rel]['rows'].append(deepcopy(o[rel]['rows'][0]));o[rel]['table']=replace(o[rel]['table'],rows=o[rel]['rows']);proof.update(duplicated_key={k:o[rel]['rows'][0][k]for k in expected_module.KEYS[rel]});changes.append({'duplicate':o[rel]['rows'][0]})
  elif name=='required-output-order':cs['fulfillment_sla'].reverse();proof['reversed_actual_compiled_sequence']=True
  elif name=='required-materialization':
   rel='fulfillment_sla';o[rel]['table']=replace(o[rel]['table'],relation_type='VIEW');proof['forbidden_mart_relation_type']='VIEW'
  elif name=='required-output-columns':
   rel='fulfillment_sla';t=o[rel]['table'];col='total_shipments';o[rel]['rows']=[{k:v for k,v in r.items()if k!=col}for r in o[rel]['rows']];o[rel]['table']=replace(t,rows=o[rel]['rows'],columns=tuple(c for c in t.columns if c!=col),physical_columns=tuple(c for c in t.physical_columns if c!=col),physical_sql_types={k:v for k,v in t.physical_sql_types.items()if k!=col},sql_types={k:v for k,v in t.sql_types.items()if k!=col});proof['missing_required_column']=col
  elif name in ('backend-command-order','required-profile','required-model-files'):
   zz=dict(z)
   if name=='backend-command-order':zz['backend_shell']={**z['backend_shell'],'exit_code':1};proof['backend_read_failed']=True
   else:
    zz['final_files']=dict(z['final_files']);missing='profiles.yml'if name=='required-profile'else'models/marts/analytics/fulfillment_summary.sql';zz['final_files'].pop(missing);proof['removed_required_actual_file']=missing
   answer=generic_workflow(zz,policy(TASK));assert answer['reward']==0;w={'status':'fail','reason':'Explicit invalid provenance fixture','details':answer}
  elif name in ('datediff-workflow','explicit-double-division-guideline'):
   pp=deepcopy(pr['nodes'])
   for record in pp:
    for node in form.walk(record['ast']):
     if name=='datediff-workflow' and node.get('function_name')in('datediff','date_diff'):node['function_name']='different_function'
     if name=='explicit-double-division-guideline'and node.get('function_name')=='/':node['children']=[{'class':'CONSTANT','value':{'type':{'id':'INTEGER'},'value':3}},{'class':'CONSTANT','value':{'type':{'id':'INTEGER'},'value':2}}]
   answer=form.inspect(pp,v['commands'][-1]['manifest'],original);assert answer['status']=='fail';w={'status':'fail','reason':'Explicit parsed-form fixture','details':answer};proof['source_rule']=name
  else:raise ValueError('Unmapped required case '+name)
  proof.update(validity='business_error',tested_checker_invoked_to_define_validity=False);pf=write(out/(rid+'-proof.json'),proof);ar=write(out/(rid+'-audit.json'),score(o,w,cs));assert io.read_json(ROOT/ar['path'])['reward']==0,rid;records.append({'requirement':rid,'independent_proof':pf,'audit':ar,'reward':0})
 # Extra comparator controls for full typed interfaces and exact key association.
 equivalents=[]
 o=deepcopy(original)
 for rel in o:o[rel]['rows'].reverse();o[rel]['table']=replace(o[rel]['table'],rows=o[rel]['rows'])
 ar=write(out/'unordered-observation-control.json',score(o));assert io.read_json(ROOT/ar['path'])['reward']==1;equivalents.append(ar)
 initialsr,initial=outputs(z,'initial') if False else (ref(trial/'agent/audit-initial/snapshot.json'),None)
 # Reuse generic loader with an explicit stage map alias, restoring it afterward.
 import review_support_v3 as support
 support.STAGES['initial']='agent/audit-initial'
 try:_,initial=outputs(z,'initial')
 finally:del support.STAGES['initial']
 ar=write(out/'initial-audit.json',score(initial,{'status':'fail','reason':'Initial environment has no authored completed build'},{}));assert io.read_json(ROOT/ar['path'])['reward']==0
 rr=write(out/'fixture-review.json',{'task_key':KEY,'cases':records,'all_detected':len(records),'expected_cases':len(ids),'passed':True,'initial_audit':ar,'initial_snapshot':initialsr,'equivalents':equivalents,'actual_positive':ref(P/'fresh-v2/fresh-review.json'),'parsed_workflow':ref(P/'parser-v1/result.json'),'workflow':form_result,'model_requests':0,'code_refs':[ref(Path(__file__)),ref(C/'expected_v5.py'),ref(C/'audit_v1.py'),ref(C/'workflow_v1.py')]});print(rr)
if __name__=='__main__':main()
