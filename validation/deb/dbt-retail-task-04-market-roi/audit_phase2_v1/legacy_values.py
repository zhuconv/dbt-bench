"""Bounded value-only replay of hash-bound historical projected observer rows.

Older capture lacks SQL DESCRIBE. It cannot certify physical types or a legal
submission. A definite independently recomputed value error may still reject.
"""
from pathlib import Path
from decimal import Decimal
import hashlib,json

def compare(request,root,derive,expected,decode):
 r=request['legacy_value_snapshot'];p=root/r['path'];data=p.read_bytes();assert hashlib.sha256(data).hexdigest()==r['sha256'];snap=json.loads(data);assert snap['task_key']==request['task_key']and snap['stage']==request['stage'];items=[v for v in snap['outputs']if v['specification']['relation']=='main.rpt_channel_roi_payback_monthly'and v['specification']['database']=='/app/database/retail.duckdb'];assert len(items)==1;item=items[0];assert not item.get('error');name=item['file'];assert Path(name).name==name and not(p.parent/name).is_symlink();raw=(p.parent/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256'];columns=[v['name'].lower()for v in item['schema']];assert len(columns)==len(set(columns));rows=[]
 for line in raw.splitlines():
  values=json.loads(line);assert len(values)==len(columns);row=dict(zip(columns,map(decode,values)))
  for n in derive.INTS:
   x=row.get(n)
   if x is None:continue
   if type(x)not in(int,float,Decimal)or not Decimal(str(x)).is_finite()or Decimal(str(x))!=Decimal(str(x)).to_integral_value():return {'status':'scored','reward':0.0,'reason':'Observed nonintegral/nonfinite count in real pre-verifier capture','field':n}
   row[n]=int(x)
  rows.append(row)
 assert len(rows)==item['rows'];result=derive.compare_output(expected,rows);return {'status':'scored','reward':float(result['equal']),'comparison':result,'scope':'Historical value projection only; no absent SQL metadata inferred. A value pass cannot establish a legal overall submission.'}
