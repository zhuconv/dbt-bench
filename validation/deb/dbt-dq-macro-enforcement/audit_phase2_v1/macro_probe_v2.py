"""Execute isolated candidate macro fixtures; render both declared backend branches."""
import json,re
from types import SimpleNamespace
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import nodes
import duckdb
from expected_v1 import parse_patterns

def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values():yield from walk(v)
    elif isinstance(x,list):
        for v in x:yield from walk(v)
def check(s,r,**kw):return dict(status=s,reason=r,**kw)
def probe(files,initial_macro):
    checks={};rendered={};env=SandboxedEnvironment()
    try:
        add=files['macros/data_quality/add_dq_flags.sql'];dq=files['macros/data_quality/dq_checks.sql']
        for backend in ['duckdb','snowflake']:
            tree=env.parse(dq);tree.body=[n for n in tree.body if not isinstance(n,nodes.Output)]
            mod=env.from_string(tree).make_module({'target':SimpleNamespace(type=backend)})
            rendered[backend]={'regex':str(mod.dq_flag_potential_pii('x')),'iqr':str(mod.dq_flag_iqr_outlier('x',['g']))}
        with duckdb.connect(':memory:',config={'enable_external_access':False})as c:
            def ast(sql):
                sts=c.extract_statements(sql)
                if len(sts)!=1 or sts[0].type!=duckdb.StatementType.SELECT:raise ValueError('Not a single read-only SELECT fixture')
                return json.loads(c.execute('SELECT json_serialize_sql(?)',[sql]).fetchone()[0])
            def query(sql):ast(sql);return c.execute(sql).fetchall()
            m=env.from_string(add).make_module({});frag=str(m.add_dq_flags(['x','y']));sql='SELECT '+frag+" FROM (VALUES (NULL::VARCHAR,'ok'),('','ok'),('   ','ok'),('ok',NULL::VARCHAR),('ok','ok')) t(x,y)"
            result=query(sql);expected=[(False,True),(False,True),(False,True),(False,True),(True,False)]
            cols=[x[0]for x in c.description];got=[(dict(zip(cols,row))['dq_is_valid'],dict(zip(cols,row))['dq_missing_required'])for row in result]
            checks['empty_required']=check('pass'if got==expected else'fail','Executed candidate macro on NULL, empty, whitespace, second missing field, and complete row',actual=got,expected=expected,sql=sql)
            patterns=parse_patterns(initial_macro)
            if len(patterns)!=4:raise ValueError('Initial public macro patterns missing')
            strings=[None,'ordinary note','alice@example.com',r'alice@example\xcom',r'\b\ddd-\dd-\dddd\b']
            values=','.join('(NULL)'if s is None else"('"+s.replace("'","''")+"')"for s in strings)
            sql='SELECT '+rendered['duckdb']['regex']+' AS flag FROM (VALUES '+values+') t(x)';actual=[r[0]for r in query(sql)];expected=[next((label for pat,label in patterns if s is not None and re.search(pat,s)),None)for s in strings]
            checks['regex_values']=check('pass'if actual==expected else'fail','Candidate DuckDB macro executed; expected labels from Python regex and original public patterns',actual=actual,expected=expected,sql=sql)
            sql='SELECT g,x,'+rendered['duckdb']['iqr']+" AS flag FROM (VALUES ('a',0.0),('a',1.0),('a',2.0),('a',3.0),('a',100.0),('b',100.0),('b',100.0),('b',100.0),('b',100.0),('b',100.0)) t(g,x)"
            actual=query(sql);expected=[None,None,None,None,'HIGH_OUTLIER']+[None]*5
            checks['iqr_values']=check('pass'if [r[2]for r in actual]==expected else'fail','Two partition hand fixture: group a quartiles 1/3, upper fence 6; group b fence 100',actual=actual,expected=expected,sql=sql)
            for backend in ['duckdb','snowflake']:
                rg=ast('SELECT '+rendered[backend]['regex']+' FROM (VALUES (NULL::VARCHAR)) t(x)');calls=[x.get('function_name','').lower()for x in walk(rg)if x.get('class')=='FUNCTION'];want='regexp_matches'if backend=='duckdb'else'regexp_like'
                checks[backend+'_regex_dispatch']=check('pass'if want in calls and('regexp_like'if backend=='duckdb'else'regexp_matches')not in calls else'fail','Parsed rendered branch function names; no claim of Snowflake execution',calls=calls)
                iq=rendered[backend]['iqr'];tree=ast('SELECT '+iq+' FROM (VALUES (1,1)) t(g,x)');calls=[x.get('function_name','').lower()for x in walk(tree)if x.get('class')in ['FUNCTION','WINDOW']]
                # Parser normalizes ordered percentile_cont to quantile_cont. Its parsed order keys distinguish SQL forms.
                orders=[x.get('orders')for x in walk(tree)if x.get('function_name','').lower()=='quantile_cont']
                if backend=='duckdb':ok='quantile_cont'in calls and not any(orders)
                else:ok='quantile_cont'in calls and bool(orders)and all(orders)
                checks[backend+'_percentile_dispatch']=check('pass'if ok else'fail','Parsed rendered percentile argument/order form; Snowflake branch rendered/parsed only',calls=calls,order_nodes=orders)
    except Exception as e:
        checks['probe_execution']=check('pending','Candidate macro fixture could not be fully evaluated',error_type=type(e).__name__,error=str(e))
    states=[x['status']for x in checks.values()]
    return check('fail'if'fail'in states else'pending'if'pending'in states else'pass','Actual isolated macro behavior and backend dispatch',details=checks,rendered=rendered,duckdb_version=duckdb.__version__,scope='Only in-memory synthetic fixtures, external access disabled. Snowflake branch is syntax/dispatch rendering, not Snowflake runtime execution.')
