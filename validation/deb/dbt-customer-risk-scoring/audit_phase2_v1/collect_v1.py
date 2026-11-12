"""Read-only raw-input/compiled-model capture; not an expected-value generator."""
import argparse,datetime,decimal,hashlib,json,os
from pathlib import Path
import duckdb
SPEC={
'geographic':{'task_key':'deb__dbt-customer-geographic','queries':{'addresses':'SELECT address_id,customer_id,state_province,is_default_shipping FROM CUSTOMER.CUSTOMER_ADDRESSES','orders':'SELECT order_id,customer_id,grand_total,status FROM ORDERS.ORDERS'},'models':['stg_customer_addresses','stg_orders','fct_state_customers']},
'risk':{'task_key':'deb__dbt-customer-risk-scoring','queries':{'customers':'SELECT customer_id,first_name,last_name FROM main.customers','orders':'SELECT order_id,customer_id,status,payment_status,chargeback_flag,ordered_at FROM main.orders'},'models':['stg_orders_risk','stg_customers_risk','customer_risk_scores']}}
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def ref(p):return {'path':str(p),'sha256':sha(p)}
def hashes(db):return {str(p):{'exists':p.exists(),**({'sha256':sha(p),'size':p.stat().st_size}if p.exists()else{})}for p in [Path(db),Path(db+'.wal')]}
def encode(x):
 if x is None or type(x)in (str,int,bool):return x
 for cls,label in [(datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')]:
  if isinstance(x,cls):return {'$type':label,'value':str(x)if cls is not float else repr(x)}
 raise TypeError('Uncaptured scalar '+repr(type(x)))
def capture(c,sql,dest):
 statements=c.extract_statements(sql)
 if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:raise ValueError('Only one actual SELECT permitted')
 schema=c.execute('DESCRIBE '+sql).fetchall();cur=c.execute(sql);cols=[d[0]for d in cur.description];rows=cur.fetchall();dest.write_text(''.join(json.dumps([encode(x)for x in r])+'\n'for r in rows));return ref(dest)|{'columns':cols,'rows':len(rows),'sql_schema':schema,'statement_types':[str(s.type)for s in statements]}
def main():
 p=argparse.ArgumentParser();p.add_argument('--task',choices=SPEC,required=True);p.add_argument('--phase',choices=['initial','final'],required=True);p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=False);spec=SPEC[a.task];project=Path('/app/dbt_project');db=os.environ.get('DUCKDB_PATH','/app/database/retail.duckdb');record={'task_key':spec['task_key'],'phase':a.phase,'database':db,'project_exists':project.exists(),'collector':ref(Path(__file__)),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'environment':{n:os.environ.get(n)for n in ['DB_TYPE','DUCKDB_PATH','DBT_PROFILES_DIR']},'database_hashes_before':hashes(db)}
 try:
  if os.environ.get('DB_TYPE')!='duckdb':raise ValueError('Collector is only reviewed for actual DuckDB binding')
  with duckdb.connect(db,read_only=True)as c:
   record['duckdb_version']=duckdb.__version__;record['raw']={}
   for name,sql in spec['queries'].items():record['raw'][name]=capture(c,sql,out/(name+'.jsonl'))|{'source_sql':sql}
   if a.phase=='final':
    manifest=project/'target/manifest.json';record['manifest']=ref(manifest);m=json.loads(manifest.read_text());record['compiled_models']={}
    for name in spec['models']:
     nodes=[(uid,n)for uid,n in m['nodes'].items()if n.get('resource_type')=='model'and n.get('name')==name]
     if len(nodes)!=1:raise ValueError('Required model not unique: '+name)
     uid,n=nodes[0];sql=n.get('compiled_code');
     if not isinstance(sql,str):raise ValueError('Missing actual compiled SELECT: '+name)
     sp=out/(name+'.sql');sp.write_text(sql);record['compiled_models'][name]=capture(c,sql,out/(name+'.jsonl'))|{'compiled_sql':ref(sp),'unique_id':uid,'original_file_path':n['original_file_path'],'manifest_invocation_id':m['metadata'].get('invocation_id'),'order_scope':'Actual compiled SELECT return sequence; no collector ORDER BY'}
 except Exception as exc:record['capture_error']={'type':type(exc).__name__,'message':str(exc)}
 finally:
  record['database_hashes_after']=hashes(db);record['database_and_wal_unchanged']=record['database_hashes_before']==record['database_hashes_after'];record['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();record['scope']='Single parsed SELECT/read_only connection, full DB/WAL before/after hashes. No guarantee for arbitrary external-function side effects.';(out/'capture.json').write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps({'task':a.task,'phase':a.phase,'capture_error':record.get('capture_error'),'database_and_wal_unchanged':record['database_and_wal_unchanged']}))
if __name__=='__main__':main()
