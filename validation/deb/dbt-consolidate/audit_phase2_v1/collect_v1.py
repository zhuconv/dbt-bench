"""Read-only public CSV, project, metadata and compiled SELECT capture."""
import argparse,datetime,decimal,hashlib,json,os,shutil
from pathlib import Path
import duckdb
PROJECT=Path('/app/dbt_consolidate');DB=Path('/app/consolidate.duckdb')
MODELS=['stg__ads_googleads','stg__ads_metaads','stg__ads_tiktokads','int__ads_unified']
SEEDS=['googleads','metaads','tiktokads']
def ref(p):
 p=Path(p)
 with p.open('rb')as f:h=hashlib.file_digest(f,'sha256').hexdigest()
 return dict(path=str(p),sha256=h)
def state(p):return {'exists':p.exists(),**(ref(p)if p.is_file()else{})}
def encode(v):
 if v is None or type(v)in(str,int,bool):return v
 for cls,name in [(datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')]:
  if isinstance(v,cls):return {'$type':name,'value':repr(v)if cls is float else str(v)}
 raise TypeError(str(type(v)))
def capture(c,sql,p):
 statements=c.extract_statements(sql)
 if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:raise ValueError('Only one parsed SELECT may execute')
 schema=c.execute('DESCRIBE '+sql).fetchall();cur=c.execute(sql);cols=[x[0]for x in cur.description];rows=cur.fetchall();p.write_text(''.join(json.dumps([encode(v)for v in row])+'\n'for row in rows));return ref(p)|{'columns':cols,'sql_schema':schema,'rows':len(rows),'statement_types':['StatementType.SELECT']}
def main():
 p=argparse.ArgumentParser();p.add_argument('--phase',choices=['initial','final'],required=True);p.add_argument('--output',required=True);p.add_argument('--target-path',default='audit_target');a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
 r={'task_key':'deb__dbt-consolidate','kind':'consolidate','phase':a.phase,'project_root':str(PROJECT),'project_exists':PROJECT.exists(),'database':str(DB),'collector':ref(__file__),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'database_hashes_before':{str(x):state(x)for x in [DB,Path(str(DB)+'.wal')]},'environment':{k:os.environ.get(k)for k in ['DB_TYPE','DBT_PROFILES_DIR','DUCKDB_PATH']}}
 try:
  assert os.environ.get('DB_TYPE')=='duckdb'
  r['raw_csv']={};(out/'raw').mkdir()
  for name in SEEDS:
   src=Path('/app/data')/(name+'.csv');before=ref(src);dest=out/'raw'/src.name;shutil.copyfile(src,dest);assert before==ref(src)and before['sha256']==ref(dest)['sha256'];r['raw_csv'][name]={'source':before,'captured':ref(dest)}
  files=[PROJECT/x for x in ['dbt_project.yml','profiles.yml','packages.yml','package-lock.yml',a.target_path+'/manifest.json',a.target_path+'/run_results.json']]
  files +=[q for folder in ['models','macros','seeds']for q in sorted((PROJECT/folder).rglob('*'))if q.is_file()]
  r['project_files']=[]
  for src in dict.fromkeys(files):
   item={'path':str(src),'exists':src.is_file()}
   if src.is_file():dest=out/'project'/src.relative_to(PROJECT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest);item['captured']=ref(dest)
   r['project_files'].append(item)
  r['packages']=[ref(p)for p in sorted((PROJECT/'dbt_packages').rglob('*'))if p.is_file()]
  r['relations']={};r['compiled']={};r['compiled_missing']=[]
  if DB.exists():
   with duckdb.connect(str(DB),read_only=True)as c:
    r['duckdb_version']=duckdb.__version__
    for name in SEEDS+MODELS:
     meta=c.execute("SELECT table_catalog,table_schema,table_name,table_type FROM information_schema.tables WHERE table_schema='analytics' AND table_name=?",[name]).fetchall();o={'metadata':meta,'exists':bool(meta)}
     if meta:o.update(capture(c,'SELECT * FROM analytics.'+name,out/(name+'.jsonl')))
     r['relations'][name]=o
    mp=PROJECT/a.target_path/'manifest.json'
    if a.phase=='final'and mp.exists():
     m=json.loads(mp.read_text());r['manifest']=ref(mp)
     for name in MODELS:
      matches=[(uid,n)for uid,n in m['nodes'].items()if n.get('resource_type')=='model'and n.get('name')==name]
      if len(matches)!=1 or not matches[0][1].get('compiled_code'):r['compiled_missing'].append(name);continue
      uid,n=matches[0];sql=n['compiled_code'];q=out/(name+'-compiled.sql');q.write_text(sql);r['compiled'][name]=capture(c,sql,out/(name+'-compiled.jsonl'))|{'sql':ref(q),'unique_id':uid,'original_file_path':n['original_file_path'],'manifest_invocation_id':m['metadata'].get('invocation_id')}
 except Exception as e:r['capture_error']={'type':type(e).__name__,'message':str(e)}
 finally:
  r['database_hashes_after']={str(x):state(x)for x in [DB,Path(str(DB)+'.wal')]};r['database_and_wal_unchanged']=r['database_hashes_before']==r['database_hashes_after'];r['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();r['scope']='Only declared public CSVs, necessary project source/package inventory and SELECTs through read_only DuckDB. Missing initial DB is expected, never created by capture. Per-command run/test manifests must be bound separately.';(out/'capture.json').write_text(json.dumps(r,indent=2)+'\n')
 print(json.dumps({'capture_error':r.get('capture_error'),'database_and_wal_unchanged':r['database_and_wal_unchanged'],'project_exists':r['project_exists']}))
if __name__=='__main__':main()
