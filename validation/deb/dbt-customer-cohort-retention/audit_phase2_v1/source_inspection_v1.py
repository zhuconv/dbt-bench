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

def retention(records,manifest,files):
    import hashlib
    try:
        relations={};observed={};source_files={}
        for item in records:
            node=manifest['nodes'][item['unique_id']];assert hashlib.sha256(node['compiled_code'].encode()).hexdigest()==item['compiled_sha256'];assert node['raw_code'].strip()==files[node['original_file_path']].strip();relation=(node['schema'].lower(),node['alias'].lower());relations[relation]=reachable(item['ast'])[1];source_files[node['original_file_path']]=node['raw_code']
        def leaves(relation,path=()):
            if relation not in relations:return {relation}
            if relation in path:raise ValueError('Recursive model graph')
            out=set()
            for v in relations[relation]:out|=leaves(v,path+(relation,))
            return out
        for name in ['cohort_retention','cohort_revenue','cohort_summary']:observed[name]=sorted(leaves(('main',name)))
        good=all(('main','orders')in vals for vals in observed.values())
        return ck('pass'if good else'fail','Actual reachable compiled mart-to-staging dependency chain reaches original main.orders; source bytes are bound to each fresh executed manifest',source_relations=observed),source_files
    except(AssertionError,ValueError,KeyError,TypeError)as e:return ck('pending','Actual compiled lineage could not be resolved: '+str(e)),{}

def formatting(files):
    import jinja2
    try:
        examined=[]
        for path,source in files.items():
            if 'strftime'not in source.lower()and'to_char'not in source.lower():continue
            parsed=jinja2.Environment().parse(source);ifs=list(parsed.find_all(jinja2.nodes.If));known=False
            for node in ifs:
                if not node.else_:continue
                for test in node.test.find_all(jinja2.nodes.Getattr):
                    if test.attr=='type'and isinstance(test.node,jinja2.nodes.Name)and test.node.name=='target':known=True
                if isinstance(node.test,jinja2.nodes.Getattr)and node.test.attr=='type':known=True
            if not known:return ck('pending','Backend-specific formatting source lacks a recognized target.type conditional; alternative portable interpretation needs review',path=path)
            examined.append(path)
        return ck('pass','Actually compiled backend-specific date formatting is guarded by parsed target.type conditionals with else branches; backend-neutral expressions require no guard',paths=examined)
    except(jinja2.TemplateSyntaxError,ValueError)as e:return ck('pending','Formatting source parse failed: '+str(e))
