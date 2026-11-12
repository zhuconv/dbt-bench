"""Execute authored attribution models against explicitly constructed tiny inputs."""
from pathlib import Path
from types import SimpleNamespace
import argparse,json,hashlib
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined
import duckdb
class Adapter:
 def __init__(self,db):self.db=db;self.calls=[]
 def get_relation(self,database,schema,identifier):
  self.calls.append(['get_relation',schema,identifier]);exists=self.db.execute('SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?',[schema,identifier]).fetchone()[0];return schema+'.'+identifier if exists else None
 def get_columns_in_relation(self,relation):
  self.calls.append(['get_columns_in_relation',relation]);return [SimpleNamespace(name=r[0]) for r in self.db.execute('DESCRIBE '+relation).fetchall()]
def fail(message):raise ValueError(message)
def run(source,destination):
 inp=json.loads(source.read_text());out={'schema_version':1,'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'model_requests':0,'duckdb_version':duckdb.__version__,'scope':'Extra deterministic synthetic fixtures in new in-memory DuckDB connections; these are not original complete-data trials. Raw fixture values establish expected purchase sums without native or audit checker calls. Exact submitted SQL is sandbox rendered using inert adapter metadata before SELECT execution.','tasks':{}}
 for task,model in [('dbt-fix-email-attribution','rpt_email_attribution_fixed'),('dbt-fix-multi-touch-attribution','rpt_multi_touch_attribution_fixed'),('dbt-fix-paid-search-attribution','rpt_paid_search_attribution_fixed')]:
  node=inp['tasks'][task]['models'][model];cases=[]
  for kind in ('events_fallback','joined_preference','missing_optional_joined','missing_all_revenue'):
   if kind=='missing_all_revenue' and task!='dbt-fix-email-attribution':continue
   with duckdb.connect(':memory:') as db:
    db.execute("CREATE TABLE main.stg_ga__sessions(session_id VARCHAR,visitor_id VARCHAR,customer_id VARCHAR,session_start TIMESTAMP,utm_source VARCHAR,utm_medium VARCHAR,utm_campaign VARCHAR,is_converted VARCHAR,_loaded_at TIMESTAMP)")
    db.execute("INSERT INTO main.stg_ga__sessions VALUES ('s1','u1','c1','2025-11-15 10:00:00','email','cpc','campaign','true','2025-11-15')")
    if kind!='missing_all_revenue':
     db.execute('CREATE TABLE main.stg_ga__events(session_id VARCHAR,event_id VARCHAR,event_name VARCHAR,event_value DECIMAL(12,2))');db.execute("INSERT INTO main.stg_ga__events VALUES ('s1','purchase1','purchase',5),('s1','page1','page_view',99)")
    if kind in ('events_fallback','joined_preference'):
     if kind=='joined_preference':db.execute("CREATE TABLE main.int_sessions_events_joined AS SELECT 's1'::VARCHAR AS session_id,'p2'::VARCHAR AS t2_event_id,'purchase'::VARCHAR AS t2_event_name,17::DECIMAL(12,2) AS t2_event_value UNION ALL SELECT 's1','page2','page_view',88")
     else:db.execute("CREATE TABLE main.int_sessions_events_joined AS SELECT 's1'::VARCHAR AS session_id,'page_view'::VARCHAR AS t2_event_name")
    expected=17 if kind=='joined_preference' else 0 if kind=='missing_all_revenue' else 5;adapter=Adapter(db);context={'adapter':adapter,'execute':True,'target':SimpleNamespace(type='duckdb',database='memory'),'var':lambda name,default=None:default,'env_var':lambda name,default=None:'duckdb' if name=='DB_TYPE' else default,'config':lambda **kw:'','log':lambda *args,**kw:'','source':lambda schema,name:schema+'.'+name,'exceptions':SimpleNamespace(raise_compiler_error=fail)}
    case={'case':kind,'expected_purchase_revenue_per_row':str(expected),'nonpurchase_values':{'events':99,'joined':88},'expected_output_rows':5 if task=='dbt-fix-multi-touch-attribution' else 1,'tested_checker_invoked_to_define_validity':False}
    try:
     sql=SandboxedEnvironment(undefined=StrictUndefined,extensions=['jinja2.ext.do']).from_string(node['raw_code']).render(**context);statements=db.extract_statements(sql)
     if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:raise ValueError('Only a single SELECT is allowed')
     cur=db.execute(sql);cols=[c[0] for c in cur.description];rows=[dict(zip(cols,r)) for r in cur.fetchall()];case.update(actual_rows=rows,rendered_sha256=hashlib.sha256(sql.encode()).hexdigest(),status='pass' if len(rows)==case['expected_output_rows'] and all(r['attributed_revenue']==expected for r in rows) else 'fail')
    except Exception as e:case.update(status='gap',error_type=type(e).__name__,reason=str(e))
    case['adapter_calls']=adapter.calls;cases.append(case)
  out['tasks'][task]={'source_sha256':hashlib.sha256(node['raw_code'].encode()).hexdigest(),'cases':cases}
 if destination.exists():raise ValueError('Immutable fixture record exists')
 destination.write_text(json.dumps(out,indent=2,default=str)+'\n');print({k:[c['status'] for c in v['cases']] for k,v in out['tasks'].items()})
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.input,a.output)
