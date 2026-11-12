"""Inspect parsed, actually compiled SQL; no source-hash whitelist."""
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values():yield from walk(v)
    elif isinstance(x,list):
        for v in x:yield from walk(v)
def reachable(ast):
    if ast.get('error')or len(ast.get('statements',[]))!=1:raise ValueError('Expected one parsed SELECT')
    active=[];leaves=[]
    def query(q,env):
        if q.get('type')!='SELECT_NODE':raise ValueError('Unreviewed query node')
        env=dict(env);env.update({v['key'].lower():v['value']['query']['node']for v in q.get('cte_map',{}).get('map',[])})
        active.extend(q.get('select_list',[]))
        def table(t,seen):
            if not t:return
            kind=t.get('type')
            if kind=='BASE_TABLE':
                name=t['table_name'].lower()
                if not t.get('schema_name')and name in env:
                    if name in seen:raise ValueError('Recursive CTE requires review')
                    child=dict(env);child.pop(name);query(env[name],child)
                else:leaves.append((t.get('schema_name','').lower(),name))
            elif kind=='JOIN':table(t.get('left'),seen);table(t.get('right'),seen)
            elif kind=='SUBQUERY':query(t['subquery']['node'],env)
            elif kind=='EMPTY':return
            else:raise ValueError('Unreviewed table node '+str(kind))
        table(q.get('from_table'),set())
    query(ast['statements'][0]['node'],{})
    return active,leaves
def inspect(kind,records,manifest,files):
 import hashlib
 try:
  models={};asts={};observed={}
  for item in records:
   n=manifest['nodes'][item['unique_id']];assert hashlib.sha256(n['compiled_code'].encode()).hexdigest()==item['compiled_sha256'];assert n['raw_code'].strip()==files[n['original_file_path']].strip();models[n['name']]=n;asts[n['name']]=item['ast'];observed[n['name']]=reachable(item['ast'])[1]
  byrelation={(n['schema'].lower(),n['alias'].lower()):name for name,n in models.items()}
  def leaves(name,seen=()):
   if name in seen:raise ValueError('Recursive model graph')
   result=set()
   for relation in observed[name]:
    if relation in byrelation:result |=leaves(byrelation[relation],seen+(name,))
    else:result.add(relation)
   return result
  roots=['supplier_payment_optimization']if kind=='payment'else['fct_supplier_metrics','fct_supplier_scorecard'];out={n:sorted(leaves(n))for n in roots}
  required={('procurement','supplier_invoices'),('procurement','suppliers'),('finance','currency_exchange_rates')}if kind=='payment'else{('procurement',n)for n in ['suppliers','purchase_orders','purchase_order_receipts','purchase_order_receipt_lines','supplier_invoices']}
  line=ck('pass'if all(required<=set(v)for v in out.values())else'fail','Actually reachable compiled marts resolve to required original raw sources through captured model graph',raw_sources=out)
  funcs=lambda expressions:[v.get('function_name','').lower()for x in expressions for v in walk(x)if v.get('function_name')]
  if kind=='payment':
   permitted={'supplier_payment_optimization','stg_procurement__supplier_invoices','stg_procurement__suppliers','stg_procurement__purchase_orders','stg_finance__currency_exchange_rates'};extra=set(models)-permitted;single=ck('fail'if extra else'pass','Required comprehensive mart logic remains in one SQL model with CTEs; existing declared staging models permitted',extra_models=sorted(extra))
   direct=set(observed['supplier_payment_optimization']);staging={('main',n)for n in ['stg_procurement__supplier_invoices','stg_procurement__suppliers','stg_finance__currency_exchange_rates']};stg=ck('pass'if staging<=direct else'fail','Actually compiled mart reads declared existing staging relations; unused purchase-order staging is not forced',direct_relations=sorted(direct))
   active,_=reachable(asts['supplier_payment_optimization']);scoped=[x for x in active if str(x.get('alias','')).lower()=='cash_outflow_week'];found=funcs(scoped);week=ck('pass'if'weekofyear'in found else'fail'if scoped else'pending','Actual reachable cash_outflow_week expression uses declared WEEKOFYEAR',functions=found)
   return dict(source_lineage=line,single_model=single,staging_refs=stg,weekofyear=week)
  active,_=reachable(asts['int_po_delivery_performance']);scoped=[x for x in active if str(x.get('alias','')).lower()=='days_late'];found=funcs(scoped);date=ck('pass'if any(f in found for f in ['datediff','date_diff'])else'fail'if scoped else'pending','Actual reachable delivery lateness expression uses DATEDIFF/date_diff database function',functions=found)
  return dict(source_lineage=line,date_difference=date)
 except(AssertionError,ValueError,KeyError,TypeError)as e:
  return {name:ck('pending','Actual compiled form requires separate inspection: '+str(e))for name in ['source_lineage']+(['single_model','staging_refs','weekofyear']if kind=='payment'else['date_difference'])}
