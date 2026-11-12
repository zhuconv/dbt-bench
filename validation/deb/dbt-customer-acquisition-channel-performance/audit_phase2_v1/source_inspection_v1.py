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
  roots=['int_session_funnel','funnel_conversion_rates','funnel_dropoff_analysis']if kind=='funnel'else['rpt_customer_acquisition_channel_performance_fixed'];required={('main',n)for n in ['sessions','events','carts']}if kind=='funnel'else{('main','int_sales__orders_enriched'),('main','stg_orders__orders')};out={n:sorted(leaves(n))for n in roots};result={'source_lineage':ck('pass'if all(required<=set(v)for v in out.values())else'fail','Actual reachable compiled roots read all declared task input relations',observed_sources=out)}
  if kind=='acquisition':
   # Parse actual Jinja calls, and require their assigned identifiers to be emitted in the model; compiled leaves bound above.
   from jinja2 import Environment,nodes
   n=models[roots[0]];tree=Environment().parse(files[n['original_file_path']]);calls=[];used={x.name for output in tree.find_all(nodes.Output)for x in output.find_all(nodes.Name)}
   for assignment in tree.find_all(nodes.Assign):
    x=assignment.node
    if isinstance(x,nodes.Call)and isinstance(x.node,nodes.Getattr)and isinstance(x.node.node,nodes.Name)and x.node.node.name=='adapter'and x.node.attr=='get_relation':
     args={a.key:a.value.value for a in x.kwargs if isinstance(a.value,nodes.Const)};calls.append({'variable':getattr(assignment.target,'name',None),'literal_kwargs':args,'emitted':getattr(assignment.target,'name',None)in used})
   ids={c['literal_kwargs'].get('identifier')for c in calls if c['emitted']and c['literal_kwargs'].get('schema')=='main'};result['adapter_get_relation']=ck('pass'if {'int_sales__orders_enriched','stg_orders__orders'}<=ids else'fail','Actual Jinja assignment calls and emitted relation variables, corroborated by compiled direct sources',calls=calls)
  return result
 except(AssertionError,KeyError,ValueError,TypeError)as e:return {'source_lineage':ck('gap','Executed compiled source inspection cannot establish form: '+str(e))}
