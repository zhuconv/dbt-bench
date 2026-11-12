"""Execute typed-submission mutations against original-input audit components."""
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from datetime import date,datetime,timedelta
from decimal import Decimal
import argparse,json
from dbt_bench import io
from run_components_v5 import load,expected_for,evaluate
from review_support_v3 import verify_trial,outputs
R=Path.cwd()
def altered(original,name,rows=None,drop=None,types=None):
 result=dict(original);item=dict(result[name]);t=item['table'];rows=rows if rows is not None else deepcopy(t.rows)
 if drop:
  rows=[{k:v for k,v in r.items() if k!=drop} for r in rows];t=replace(t,columns=tuple(c for c in t.columns if c!=drop),physical_columns=tuple(c for c in t.physical_columns if c!=drop),sql_types={c:v for c,v in t.sql_types.items() if c!=drop},physical_sql_types={c:v for c,v in t.physical_sql_types.items() if c!=drop})
 if types:t=replace(t,sql_types={**t.sql_types,**types},physical_sql_types={**t.physical_sql_types,**types})
 t=replace(t,rows=rows);item.update(table=t,rows=rows);result[name]=item;return result
def run(report,out):
 r=io.read_json(report);task=r['task_key'].removeprefix('deb__');batch=Path(r['batch']['path']).parent;v=verify_trial(batch,r['trials'][0]['trial']);e,a,code=load(task);expected,raw=expected_for(task,e,a,v);sr,o=outputs(v,'agent-submission');w={'status':'pass','reason':'Component fixture: actual workflow is separately graded in six-stage record'};baseline=evaluate(task,a,o,expected,w);cases=[]
 def test(label,changed,proof,kind='negative'):
  ans=evaluate(task,a,changed,expected,w);cases.append({'case':label,'kind':kind,'independent_proof':proof,'tested_checker_invoked_to_define_validity':False,'audit_reward':ans['reward'],'rejected':ans['reward']==0,'failed_checks':[k for k,z in ans['checks'].items() if z['status']=='fail'],'scope':'Synthetic table-submission fixture from the hash-bound actual original-data output, never claimed a new benchmark/Harbor trial.'})
 for name,item in o.items():
  if not item.get('rows'):continue
  rows=item['rows'];t=item['table'];cols=list(t.columns)
  test(name+'.duplicate',altered(o,name,rows=deepcopy(rows)+[deepcopy(rows[0])]),{'rule':'Declared unique model grain or source multiplicity','rows_before':len(rows),'rows_after':len(rows)+1,'exact_duplicate_added':True})
  test(name+'.missing_row',altered(o,name,rows=deepcopy(rows[1:])),{'rule':'Source-derived required population','rows_before':len(rows),'rows_after':len(rows)-1})
  # Each column of a fully accepted component is perturbed individually. A key
  # edit establishes a wrong association; a value edit changes its known value.
  if baseline['reward']==1:
   for col in cols:
    index=next((i for i,row in enumerate(rows) if row[col] is not None),None)
    if index is None:continue
    changed=deepcopy(rows);old=changed[index][col]
    if type(old) is bool:new=not old
    elif type(old) in (int,float,Decimal):new=old+1000000
    elif isinstance(old,(date,datetime)):new=old+timedelta(days=20000)
    elif isinstance(old,str):new='__phase4_wrong_value__'+old
    else:continue
    changed[index][col]=new;test(name+'.value.'+col,altered(o,name,rows=changed),{'rule':'Exactly one source-associated field differs from independently verified input-derived output','column':col,'before':str(old),'after':str(new),'changed_cells':1})
  # Structural comparators are exercised even if business interpretation is gap.
  key=cols[0];test(name+'.missing_column',altered(o,name,drop=key),{'rule':'Required declared column removed','column':key})
  bad=deepcopy(rows);bad[0][key]=str(bad[0][key]);test(name+'.wrong_type',altered(o,name,rows=bad,types={key:'BLOB'}),{'rule':'Required text/date/numeric logical field exposed with incompatible BLOB SQL type','column':key})
  # Physical column order is not prescribed by these contracts; changing only
  # metadata and dictionary presentation preserves complete row relationships.
  perm=dict(o);it=dict(item);it['table']=replace(t,columns=tuple(reversed(t.columns)),physical_columns=tuple(reversed(t.physical_columns)));perm[name]=it
  test(name+'.column_order',perm,{'rule':'Same field names/types/values/row order; only column presentation reversed','rows':len(rows)},'equivalent')
 result={'schema_version':1,'task_key':r['task_key'],'actual_report':io.evidence_ref(report,R),'snapshot':sr,'code_refs':[io.evidence_ref(p,R) for p in [Path(__file__),*code]],'baseline_component_reward':baseline['reward'],'cases':cases,'model_requests':0,'scope':'Audit component regression; actual workflow and native scores stay in separate formal records. A gap baseline is never converted to accepted by these fixtures.'}
 result['counts']={'executed':len(cases),'negative':sum(c['kind']=='negative' for c in cases),'rejected_negative':sum(c['kind']=='negative' and c['rejected'] for c in cases),'equivalent':sum(c['kind']=='equivalent' for c in cases),'accepted_equivalent':sum(c['kind']=='equivalent' and c['audit_reward']==1 for c in cases)}
 if out.exists():raise ValueError('Immutable fixture result exists')
 out.write_text(json.dumps(result,indent=2,default=str)+'\n');print(json.dumps({'task_key':r['task_key'],**result['counts']}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.report,a.output)
