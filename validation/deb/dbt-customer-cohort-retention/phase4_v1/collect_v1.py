"""Read-only explicitly declared input/output capture for phase4 DEB reviews."""
import argparse,datetime,decimal,hashlib,json,os
from pathlib import Path
import duckdb

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def ref(p):return {'path':str(p),'sha256':sha(p)}
def hashes(db):return {str(p):{'exists':p.exists(),**({'sha256':sha(p),'size':p.stat().st_size} if p.exists() else {})} for p in (Path(db),Path(db+'.wal'))}
def encode(v):
 if v is None or type(v) in (str,int,bool):return v
 for cls,tag in ((datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')):
  if isinstance(v,cls):return {'$type':tag,'value':repr(v) if cls is float else str(v)}
 raise TypeError(str(type(v)))
def capture(c,sql,p):
 stmts=c.extract_statements(sql)
 if len(stmts)!=1 or stmts[0].type!=duckdb.StatementType.SELECT:raise ValueError('Expected exactly one SELECT')
 schema=c.execute('DESCRIBE '+sql).fetchall();cur=c.execute(sql);columns=[x[0] for x in cur.description];rows=cur.fetchall()
 p.write_text(''.join(json.dumps([encode(v) for v in row])+'\n' for row in rows))
 return {**ref(p),'columns':columns,'rows':len(rows),'sql_schema':schema,'statement_types':[str(x.type) for x in stmts]}
def main():
 p=argparse.ArgumentParser();p.add_argument('--spec-json',required=True);p.add_argument('--phase',choices=['initial','final'],required=True);p.add_argument('--target-path',default='phase4_target_v1');p.add_argument('--output',required=True);a=p.parse_args()
 spec=json.loads(a.spec_json);out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
 project=Path(spec['project_root']);db=spec['database'];r={'schema_version':1,'task_key':spec['task_key'],'phase':a.phase,'collector':ref(__file__),'project_root':str(project),'target_path':a.target_path,'database':db,'database_hashes_before':hashes(db),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'environment':{k:os.environ.get(k) for k in ('DB_TYPE','DUCKDB_PATH','DBT_PROFILES_DIR')},'raw':{},'physical_outputs':{},'initial_outputs':{},'project_files':[],'raw_errors':{},'output_errors':{}}
 try:
  assert os.environ.get('DB_TYPE')=='duckdb'
  files=list(dict.fromkeys([*spec['files'],a.target_path+'/manifest.json',a.target_path+'/run_results.json']))
  for name in files:
   rel=Path(name)
   if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe capture path')
   original=project/rel;row={'path':str(original),'exists':original.is_file()}
   if row['exists']:
    if original.stat().st_size>32*1024*1024:raise ValueError('Capture exceeds size limit')
    dest=out/'project'/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(original.read_bytes());row.update(captured=ref(dest),bytes=dest.stat().st_size)
   r['project_files'].append(row)
  with duckdb.connect(db,read_only=True) as c:
   r['duckdb_version']=duckdb.__version__;r['relation_metadata']=[];r['column_metadata']=[]
   for key,sql in spec['raw'].items():
    try:r['raw'][key]={**capture(c,sql,out/('raw-'+key+'.jsonl')),'source_sql':sql}
    except Exception as e:r['raw_errors'][key]={'type':type(e).__name__,'message':str(e)}
   for name,relation in spec['outputs'].items():
    schema,table=relation.split('.',1)
    meta=c.execute('SELECT table_catalog,table_schema,table_name,table_type FROM information_schema.tables WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?)',[schema,table]).fetchall();r['relation_metadata']+=meta
    r['column_metadata']+=c.execute('SELECT table_schema,table_name,column_name,data_type,ordinal_position FROM information_schema.columns WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?) ORDER BY ordinal_position',[schema,table]).fetchall()
    if meta:
     field='physical_outputs' if a.phase=='final' else 'initial_outputs'
     try:r[field][name]=capture(c,'SELECT * FROM '+relation,out/(name+'-physical.jsonl'))
     except Exception as e:r['output_errors'][name]={'type':type(e).__name__,'message':str(e)}
    elif a.phase=='final':r['output_errors'][name]={'type':'MissingRelation','message':relation}
  mp=project/a.target_path/'manifest.json'
  if a.phase=='final' and mp.exists():
   m=json.loads(mp.read_text());r['manifest']=ref(mp);r['compiled_sources']={}
   for name in spec['outputs']:
    nodes=[(uid,n) for uid,n in m['nodes'].items() if n.get('resource_type')=='model' and n['name']==name]
    if len(nodes)==1:
     uid,node=nodes[0];sql=node.get('compiled_code');r['compiled_sources'][name]={'unique_id':uid,'original_file_path':node['original_file_path'],'compiled_available':sql is not None}
     if sql is not None:
      dest=out/(name+'-compiled.sql');dest.write_text(sql);r['compiled_sources'][name]['source']=ref(dest)
 except Exception as e:r['capture_error']={'type':type(e).__name__,'message':str(e)}
 finally:
  r['database_hashes_after']=hashes(db);r['database_and_wal_unchanged']=r['database_hashes_before']==r['database_hashes_after'];r['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();r['scope']='Explicit named raw relations and required outputs; only SELECT on read-only DuckDB; exact SQL types and duplicates retained. Missing relations/errors are explicit. No /app or database export.'
  (out/'capture.json').write_text(json.dumps(r,indent=2)+'\n')
 print(json.dumps({k:r.get(k) for k in ('capture_error','raw_errors','output_errors','database_and_wal_unchanged')}))
if __name__=='__main__':main()
