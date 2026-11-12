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
def repeat(records,manifest,files):
    import hashlib,jinja2
    try:
        assert len(records)==1
        item=records[0];node=manifest['nodes'][item['unique_id']];assert hashlib.sha256(node['compiled_code'].encode()).hexdigest()==item['compiled_sha256'];source=files[node['original_file_path']];assert node['raw_code'].strip()==source.strip();leaves=reachable(item['ast'])[1]
        raw=ck('pass'if ('main','int_sales__orders_enriched')in leaves else'fail','Reachable actual compiled SELECT reads required main source; installed SQL bound to trusted executed manifest',relations=leaves)
        ast=jinja2.Environment().parse(source);calls=[]
        for call in ast.find_all(jinja2.nodes.Call):
            f=call.node
            if isinstance(f,jinja2.nodes.Getattr)and f.attr=='get_relation'and isinstance(f.node,jinja2.nodes.Name)and f.node.name=='adapter':
                calls.append({v.key:v.value.value if isinstance(v.value,jinja2.nodes.Const)else'<expression>'for v in call.kwargs})
        ok=any(c.get('schema')=='main'and c.get('identifier')=='int_sales__orders_enriched'for c in calls)
        return raw,ck('pass'if ok else'pending','Parsed actual source get_relation call for main enriched input; actual compiled reachable source independently bound. Other introspection forms require actual review.',calls=calls)
    except(AssertionError,ValueError,KeyError,TypeError,jinja2.TemplateSyntaxError)as e:return ck('pending','Actual compiled lineage unresolved: '+str(e)),ck('pending','Introspection unresolved: '+str(e))
