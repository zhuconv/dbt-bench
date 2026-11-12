"""Read-only, lossless original-input and fresh compiled-output capture."""
import argparse,datetime,decimal,hashlib,json,os
from pathlib import Path
import duckdb
QUERIES={'sessions':'SELECT session_id,visitor_id,session_start,is_converted,utm_source,utm_medium,utm_campaign FROM main.stg_ga__sessions','events':'SELECT session_id,event_name,event_value FROM main.stg_ga__events'}
UPSTREAM=('stg_ga__sessions','stg_ga__events','int_sessions_events_joined')
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def ref(p):return {'path':str(p),'sha256':sha(p)}
def hashes(db):return {str(p):{'exists':p.exists(),**({'sha256':sha(p),'size':p.stat().st_size}if p.exists()else{})}for p in [Path(db),Path(db+'.wal')]}
def encode(x):
 if x is None or type(x)in(str,int,bool):return x
 for cls,name in [(datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')]:
  if isinstance(x,cls):return {'$type':name,'value':str(x)if cls is not float else repr(x)}
 raise TypeError(str(type(x)))
def capture(c,sql,dest):
 stmts=c.extract_statements(sql)
 if len(stmts)!=1 or stmts[0].type!=duckdb.StatementType.SELECT:raise ValueError('Only single parsed SELECT permitted')
 schema=c.execute('DESCRIBE '+sql).fetchall();cur=c.execute(sql);cols=[d[0]for d in cur.description];rows=cur.fetchall();dest.write_text(''.join(json.dumps([encode(v)for v in r])+'\n'for r in rows));return ref(dest)|{'columns':cols,'rows':len(rows),'sql_schema':schema,'statement_types':[str(s.type)for s in stmts]}
def upstream(out,root):
 root=Path(root);files=[]
 if root.exists():
  paths=[p for p in root.glob('models/**/*.sql')if p.stem in UPSTREAM]
  paths +=[root/n for n in ['dbt_project.yml','profiles.yml','target/manifest.json','target/run_results.json']]
  for p in paths:
   if not p.is_file():files.append({'path':str(p),'exists':False});continue
   q=out/'upstream'/p.relative_to(root);q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(p.read_bytes());files.append({'path':str(p),'exists':True,'captured':ref(q),'bytes':q.stat().st_size})
 return {'root':str(root),'exists':root.exists(),'files':files}
def main():
 p=argparse.ArgumentParser();p.add_argument('--task',choices=['email','paid'],required=True);p.add_argument('--phase',choices=['initial','final'],required=True);p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=False);project=Path('/app/dbt_project'if a.task=='email'else os.environ.get('DBT_PROJECT_DIR_DUCKDB','/app/dbt_models_duckdb'));name='rpt_email_attribution_fixed'if a.task=='email'else'rpt_paid_search_attribution_fixed';db=os.environ.get('DUCKDB_PATH','/app/database/retail.duckdb');r={'kind':a.task,'phase':a.phase,'database':db,'project_root':str(project),'project_exists':project.exists(),'collector':ref(__file__),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'database_hashes_before':hashes(db),'environment':{k:os.environ.get(k)for k in ['DB_TYPE','DUCKDB_PATH','DBT_PROFILES_DIR','DBT_PROJECT_DIR_DUCKDB']}}
 try:
  assert os.environ.get('DB_TYPE')=='duckdb','Actual DB_TYPE must be duckdb'
  r['upstream_projects']=[upstream(out/('project-'+str(i)),root)for i,root in enumerate(dict.fromkeys(['/app/dbt_transforms',os.environ.get('DBT_PROJECT_DIR_DUCKDB','/app/dbt_models_duckdb')]))]
  with duckdb.connect(db,read_only=True)as c:
   r['duckdb_version']=duckdb.__version__;r['relation_metadata']=c.execute("SELECT table_catalog,table_schema,table_name,table_type FROM information_schema.tables WHERE lower(table_schema)='main' AND lower(table_name) IN ('stg_ga__sessions','stg_ga__events','int_sessions_events_joined') ORDER BY table_name").fetchall();r['column_metadata']=c.execute("SELECT table_schema,table_name,column_name,data_type,ordinal_position FROM information_schema.columns WHERE lower(table_schema)='main' AND lower(table_name) IN ('stg_ga__sessions','stg_ga__events','int_sessions_events_joined') ORDER BY table_name,ordinal_position").fetchall();r['raw']={}
   present={x[2].lower()for x in r['relation_metadata']}
   for key,sql in QUERIES.items():
    relation='stg_ga__'+key
    if relation in present:r['raw'][key]=capture(c,sql,out/(key+'.jsonl'))|{'source_sql':sql}
   joined_cols={x[2].lower()for x in r['column_metadata']if x[1].lower()=='int_sessions_events_joined'};r['joined_revenue_available']={'session_id','t2_event_name','t2_event_value'}<=joined_cols
   if r['joined_revenue_available']:
    sql='SELECT session_id,t2_event_name,t2_event_value FROM main.int_sessions_events_joined';r['raw']['joined']=capture(c,sql,out/'joined.jsonl')|{'source_sql':sql}
   if a.phase=='final':
    mp=project/'target/manifest.json';m=json.loads(mp.read_text());r['manifest']=ref(mp);nodes=[(uid,n)for uid,n in m['nodes'].items()if n.get('resource_type')=='model'and n.get('name')==name]
    if len(nodes)!=1:raise ValueError('Required model not unique')
    uid,n=nodes[0];sql=n['compiled_code'];sp=out/'compiled.sql';sp.write_text(sql);r['compiled']=capture(c,sql,out/'compiled.jsonl')|{'sql':ref(sp),'unique_id':uid,'original_file_path':n['original_file_path'],'manifest_invocation_id':m['metadata'].get('invocation_id')}
 except Exception as exc:r['capture_error']={'type':type(exc).__name__,'message':str(exc)}
 finally:
  r['database_hashes_after']=hashes(db);r['database_and_wal_unchanged']=r['database_hashes_before']==r['database_hashes_after'];r['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();r['scope']='Only single parsed SELECT through read_only DuckDB; complete DB/WAL before/after hash. Original input projections and declared upstream source/artifact files only; no full DB export.';(out/'capture.json').write_text(json.dumps(r,indent=2)+'\n')
 print(json.dumps({'capture_error':r.get('capture_error'),'database_and_wal_unchanged':r['database_and_wal_unchanged']}))
if __name__=='__main__':main()
