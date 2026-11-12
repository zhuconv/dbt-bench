"""Value-only fallback for actual older observer projections; never invent physical schema."""
import hashlib,json
from pathlib import Path
from decimal import Decimal

def compare(request,root,expected,details):
 from audit_tables import decode
 from dbt_bench.comparison import compare_tables,Column,TableContract
 def read(ref):
  p=root/ref['path'];raw=p.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Changed projection capture')
  return p,raw
 path,raw=read(request['snapshot']);snapshot=json.loads(raw);assert snapshot['task_key']==request['task_key']and snapshot['stage']==request['stage'];parts={}
 for model,key in [('fct_session_quality','session_id'),('rpt_session_summary','device_type'),('rpt_visitor_segments','visitor_segment')]:
  try:
   item=next(v for v in snapshot['outputs']if v['specification']['database']=='/app/database/retail.duckdb'and v['specification']['relation']=='web_analytics.'+model);assert not item.get('error');columns=[v['name']for v in item['sql_schema']];assert len(set(columns))==len(columns);projected='SELECT '+','.join('"'+n+'"'for n in columns)+' FROM "web_analytics"."'+model+'"';full='SELECT * FROM "web_analytics"."'+model+'"';assert item['query']in(projected,full),'Only exact unfiltered observer SELECT accepted';name=item['file'];assert Path(name).name==name;_,rowsraw=read({'path':str(path.parent/name),'sha256':item['sha256']});rows=[]
   for line in rowsraw.splitlines():
    values=json.loads(line);assert len(values)==len(columns);r=dict(zip([n.lower()for n in columns],map(decode,values)))
    for n in ['is_bounce','is_converted','is_returning_visitor']:
     if n in r and(type(r[n])is bool or type(r[n])in(int,float,Decimal)and Decimal(str(r[n])).is_finite()and r[n]in(0,1)):r[n]=int(r[n])
    rows.append(r)
   assert len(rows)==item['rows'];gold=[dict(r)for r in expected[model]];actual={r.get(key):r for r in rows}
   for r in gold:
    for n,allowed in details['rounding_options'].get((model,r[key]),{}).items():
     value=actual.get(r[key],{}).get(n)
     if type(value)in(int,float,Decimal)and Decimal(str(value)).is_finite()and Decimal(str(value))in allowed:r[n]=Decimal(str(value))
   columns=tuple(Column(n,'text'if n in('session_id','visitor_id','device_type','quality_tier','velocity_category','visitor_segment')else'datetime'if n=='session_start'else'numeric')for n in gold[0]);comparison=compare_tables(gold,rows,TableContract(columns,keys=(key,),allow_extra_columns=True));parts[model]={'status':'scored','reward':float(comparison.equal),'reason':comparison.reason}
  except Exception as e:parts[model]={'status':'unknown','reward':None,'reason':str(e)}
 rewards=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0
 return {'status':'unknown'if reward is None else'scored','reward':reward,'relations':parts,'scope':'Only authenticated unfiltered observer projected typed row values/grain. Physical columns/order/SQL-width/workflow are not certified by this fallback. Fact velocity remains excluded and separately unresolved.'}
