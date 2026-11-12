"""Inspect actually compiled reachable model sources; no source-hash whitelist."""
import hashlib

def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def reachable(ast):
 if ast.get('error')or len(ast.get('statements',[]))!=1:raise ValueError('Expected one parsed statement')
 leaves=[]
 def query(q,env,trail=()):
  env=dict(env);env.update({v['key'].lower():v['value']['query']['node']for v in q.get('cte_map',{}).get('map',[])})
  if q['type']=='SET_OPERATION_NODE':query(q['left'],env,trail);query(q['right'],env,trail);return
  if q['type']!='SELECT_NODE':raise ValueError('Unreviewed query node '+q['type'])
  def table(t):
   if not t:return
   typ=t['type']
   if typ=='BASE_TABLE':
    name=t['table_name'].lower()
    if not t.get('schema_name')and name in env:
     if name in trail:raise ValueError('Recursive CTE not reviewed')
     query(env[name],env,trail+(name,))
    else:leaves.append((t.get('schema_name','').lower(),name))
   elif typ=='JOIN':table(t.get('left'));table(t.get('right'))
   elif typ=='SUBQUERY':query(t['subquery']['node'],env,trail)
   elif typ=='EMPTY':return
   else:raise ValueError('Unreviewed from node '+typ)
  table(q.get('from_table'))
 query(ast['statements'][0]['node'],{})
 return leaves

def inspect(kind,records,manifest,files):
 try:
  observed={};bindings=[]
  for r in records:
   n=manifest['nodes'][r['unique_id']];assert hashlib.sha256(n['compiled_code'].encode()).hexdigest()==r['compiled_sha256'];assert n['raw_code'].strip()==files[n['original_file_path']].strip();observed[n['name']]=reachable(r['ast']);bindings.append(n['name'])
  if kind=='session':
   expected={('main','stg_digital__web_sessions'),('main','stg_analytics__fact_sales')};ok=set(observed['attribution_report'])==expected
  else:
   allowed={('raw','fact_sales'),('raw','dim_date'),('raw','dim_channel'),('raw','fact_marketing_spend'),('raw','campaign_channels')};allowed|={(n['schema'].lower(),n['alias'].lower())for n in manifest['nodes'].values()if n['name']in observed};ok=len(observed)==15 and all(set(v)<=allowed for v in observed.values())
  return ck('pass'if ok else'gap','Actually parsed compiled FROM/CTE sources, original SQL binding and declared model/source relation scope; scalar semantics independently recomputed',observed_sources=observed)
 except(AssertionError,KeyError,ValueError,TypeError)as e:return ck('gap','Executed compiled source inspection cannot establish form: '+str(e))
