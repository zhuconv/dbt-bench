"""Trace active source() relation calls with a bounded Jinja marker render and SQL AST."""
import hashlib,json
from jinja2 import nodes
from jinja2.sandbox import SandboxedEnvironment
import duckdb
def active_nodes(ast):
    result=[]
    def visit(value,inherited):
        if isinstance(value,list):
            for v in value:visit(v,inherited)
            return
        if not isinstance(value,dict):return
        ctes=dict(inherited)
        for item in value.get('cte_map',{}).get('map',[]):ctes[item['key'].lower()]=item['value']['query']['node']
        result.append(value);name=value.get('table_name','').lower()
        if value.get('type')=='BASE_TABLE'and not value.get('schema_name')and name in ctes:visit(ctes[name],{k:v for k,v in ctes.items()if k!=name})
        for key,v in value.items():
            if key!='cte_map':visit(v,ctes)
    for s in ast.get('statements',[]):visit(s['node'],{})
    return result
def probe(text):
    result={'source_sha256':hashlib.sha256(text.encode()).hexdigest(),'duckdb_version':duckdb.__version__}
    try:
        env=SandboxedEnvironment();env.globals.clear();ast=env.parse(text)
        allowed=(nodes.Template,nodes.Output,nodes.TemplateData,nodes.Call,nodes.Name,nodes.Const,nodes.Keyword,nodes.Tuple,nodes.List,nodes.Dict,nodes.Pair,nodes.If,nodes.Compare,nodes.Operand,nodes.Getattr)
        if any(not isinstance(n,allowed)for n in ast.find_all(nodes.Node)):return result|{'status':'pending','reason':'Unfamiliar Jinja control flow/indirection needs its actual dbt lineage; not rejected by source hash'}
        for item in ast.find_all(nodes.Getattr):
            if not(isinstance(item.node,nodes.Name)and item.node.name=='target'and item.attr=='type'):return result|{'status':'pending','reason':'Unrecognized attribute access is not evaluated'}
        for call in ast.find_all(nodes.Call):
            if not isinstance(call.node,nodes.Name)or call.node.name not in {'config','source','ref'}:return result|{'status':'pending','reason':'Unrecognized macro requires independent lineage inspection'}
        markers={}
        def source(*args):
            if len(args)!=2 or any(not isinstance(a,str)for a in args):raise ValueError('Source arguments not literal strings')
            name='__audit_source_'+str(len(markers))+'__';markers[name]=tuple(args);return '"'+name+'"'
        def ref(*args):return '"__audit_ref__"'
        env.globals.update(config=lambda **kwargs:'',source=source,ref=ref,target={'type':'duckdb'})
        sql=env.from_string(text).render()
        with duckdb.connect(':memory:')as c:
            statements=c.extract_statements(sql)
            if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:raise ValueError('Expected one parsed SELECT')
            parsed=json.loads(c.execute('SELECT json_serialize_sql(?)',[sql]).fetchone()[0])
        if parsed.get('error'):raise ValueError('SQL parser failed')
        active={n.get('table_name')for n in active_nodes(parsed)if n.get('type')=='BASE_TABLE'}
        used={(s,t.upper())for name,(s,t)in markers.items()if name in active};ok={('enterprise_db','ORDERS'),('enterprise_db','CHANNELS')}<=used
        return result|{'status':'pass'if ok else'fail','reason':'Parsed active query uses both required source() relation markers'if ok else'Active parsed query omits one or both required source() relation calls; comments/unused CTEs do not count','active_source_calls':sorted(used),'rendered_marker_sql':sql,'scope':'Bounded literal config/source/ref Jinja forms only; no query execution and no untrusted template attribute access.'}
    except(Exception,RecursionError)as e:return result|{'status':'pending','reason':type(e).__name__+': '+str(e)}
