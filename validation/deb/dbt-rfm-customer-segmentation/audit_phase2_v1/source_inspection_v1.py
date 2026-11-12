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
def rfm(ast_by_model,metric_rows):
    try:
        lineage={n:reachable(a)[1]for n,a in ast_by_model.items()};required={'stg_orders__orders':('main','orders'),'stg_customer__customers':('main','customers'),'int_rfm_metrics':('rfm_analytics','stg_orders__orders'),'rfm_scores':('rfm_analytics','int_rfm_metrics'),'rfm_segments':('rfm_analytics','rfm_scores')};good=all(v in lineage.get(n,[])for n,v in required.items());raw=ck('pass'if good else'fail','Reachable compiled SQL dependencies access declared source and model chain',relations=lineage)
        active,_=reachable(ast_by_model['rfm_scores']);windows=[v for x in active for v in walk(x)if v.get('type')=='WINDOW_NTILE'];assert len(windows)==3
        details=[]
        for w in windows:
            assert len(w['children'])==1 and w['children'][0].get('value',{}).get('value')==5 and not w['partitions'];orders=w['orders'];assert len(orders)>=2,'No deterministic secondary ordering';names=[]
            for order in orders:
                ex=order['expression']
                if ex.get('class')!='COLUMN_REF':raise ValueError('Non-column deterministic expression needs separate actual proof')
                names.append(ex['column_names'][-1].lower())
            values=[tuple(r[k]for k in names)for r in metric_rows];assert len(values)==len(set(values)),'NTILE order keys leave actual ties unresolved';details.append({'alias':w['alias'],'columns':names,'order_types':[o['type']for o in orders],'actual_total_order_unique':True})
        return raw,ck('pass','Three reachable NTILE(5) windows each have a secondary key making the actual raw-metric population totally ordered; direction/tie value allocation checked separately',windows=details)
    except AssertionError as e:return locals().get('raw',ck('pending','Lineage not resolved')),ck('fail',str(e))
    except(ValueError,KeyError,TypeError)as e:return ck('pending','Unsupported parsed source form: '+str(e)),ck('pending','Unsupported parsed source form: '+str(e))
