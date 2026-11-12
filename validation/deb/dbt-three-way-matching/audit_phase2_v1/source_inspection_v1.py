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
  name='ts_sales__channel_revenue_margin_monthly'if kind=='channel'else'three_way_match';r=next(r for r in records if r['model']==name);n=manifest['nodes'][r['unique_id']];assert hashlib.sha256(n['compiled_code'].encode()).hexdigest()==r['compiled_sha256'];assert n['raw_code'].strip()==files[n['original_file_path']].strip();seen=set(reachable(r['ast']));allowed={('main',x)for x in (['dim_fact_sales','stg_fact_sales','dim_dim_channel','stg_analytics__dim_date']if kind=='channel'else['stg_procurement__purchase_order_lines','stg_procurement__purchase_orders','stg_procurement__purchase_order_receipts','stg_procurement__purchase_order_receipt_lines','stg_procurement__supplier_invoices','stg_procurement__supplier_invoice_lines','stg_procurement__suppliers'])};return ck('pass'if seen==allowed else'fail','Actually compiled required target reachable FROM relations must be exactly declared provided sources; scalar semantics evaluated independently',actual_relations=sorted(seen),required_relations=sorted(allowed))
 except(AssertionError,KeyError,ValueError,TypeError,StopIteration)as e:return ck('gap','Executed parser source review unresolved: '+str(e))
