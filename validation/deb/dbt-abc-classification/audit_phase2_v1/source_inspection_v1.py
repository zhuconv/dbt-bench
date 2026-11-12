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
  models={};observed={}
  for r in records:
   n=manifest['nodes'][r['unique_id']];assert hashlib.sha256(n['compiled_code'].encode()).hexdigest()==r['compiled_sha256'];assert n['raw_code'].strip()==files[n['original_file_path']].strip();models[n['name']]=n;observed[n['name']]=reachable(r['ast'])
  relationmap={(n['schema'].lower(),n['alias'].lower()):name for name,n in models.items()}
  def leaves(name,seen=()):
   if name in seen:raise ValueError('Recursive model dependency')
   out=set()
   for r in observed[name]:out|=leaves(relationmap[r],seen+(name,))if r in relationmap else{r}
   return out
  roots=['purchase_costs_usd','sale_cogs','inventory_turnover_metrics']if kind=='lifo'else['int_abc_product_revenue','abc_classification','abc_summary','abc_category_breakdown'];out={n:sorted(leaves(n))for n in roots};allowed={('main',n)for n in (['stg_procurement__purchase_orders','stg_procurement__purchase_order_lines','stg_orders__orders','stg_orders__order_lines','stg_finance__currency_exchange_rates']if kind=='lifo'else['stg_orders__orders','stg_orders__order_lines','stg_product__products'])};allowed|={('orders','orders'),('orders','order_lines'),('procurement','purchase_orders'),('procurement','purchase_order_lines'),('finance','currency_exchange_rates'),('product','products')};ok=all(v and set(v)<=allowed for v in out.values());return ck('pass'if ok else'gap','Reachable compiled output source leaves checked against declared input staging/physical sources; value semantics are independent checks',observed_sources=out)
 except(AssertionError,KeyError,ValueError,TypeError)as e:return ck('gap','Executed compiled source inspection cannot establish form: '+str(e))
