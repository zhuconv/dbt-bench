"""Reject known wrong channel choices in authenticated old observer projections only."""
from pathlib import Path
import hashlib,json

def inspect(request,root,allowed):
 from audit_tables import decode
 def read(ref):
  p=root/ref['path'];data=p.read_bytes()
  if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Changed capture')
  return p,data
 p,data=read(request['snapshot']);snapshot=json.loads(data);assert snapshot['task_key']==request['task_key']and snapshot['stage']==request['stage'];matches=[o for o in snapshot['outputs']if o['specification']['database']=='/app/database/retail.duckdb'and o['specification']['relation']=='analytics.rpt_cac_payback_waterfall_fixed'];assert len(matches)==1;item=matches[0];assert not item.get('error');cols=[c['name']for c in item['schema']];assert len(cols)==len(set(cols))and {'campaign_id','channel_key'}<=set(cols);query='SELECT '+','.join('"'+n+'"'for n in cols)+' FROM "analytics"."rpt_cac_payback_waterfall_fixed"';assert item['query']in(query,'SELECT * FROM "analytics"."rpt_cac_payback_waterfall_fixed"');name=item['file'];assert Path(name).name==name;_,raw=read({'path':str(p.parent/name),'sha256':item['sha256']});rows=[]
 for line in raw.splitlines():
  values=json.loads(line);assert len(values)==len(cols);rows.append(dict(zip(cols,map(decode,values))))
 assert len(rows)==item['rows'];errors=[{'campaign_id':r['campaign_id'],'channel_key':r['channel_key'],'allowed':allowed[r['campaign_id']]}for r in rows if r['campaign_id']in allowed and r['channel_key']not in allowed[r['campaign_id']]]
 return {'status':'scored'if errors else'unknown','reward':0.0 if errors else None,'wrong_channel_rows':len(errors),'examples':errors[:5],'scope':'Actual hash-bound unfiltered projection proves non-maximal channel choice only. No full physical schema, whole-row correctness or workflow certification.'}
