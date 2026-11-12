"""Bounded declared-input collector. No target mutation or model execution."""
import argparse,datetime,decimal,hashlib,json,os
from pathlib import Path
import duckdb
RAW={'funnel':{'sessions':'SELECT session_id,visitor_id,customer_id,session_start,is_converted FROM main.sessions','events':'SELECT session_id,event_type,event_name FROM main.events','carts':'SELECT session_id,status FROM main.carts'},'acquisition':{'enriched_orders':'SELECT order_id,customer_id,ordered_at,grand_total,status FROM main.int_sales__orders_enriched','orders':'SELECT order_id,attribution_channel,utm_source,utm_medium FROM main.stg_orders__orders'}}
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def ref(p):return dict(path=str(p),sha256=sha(p))
def encode(x):
 if x is None or type(x)in[str,int,bool]:return x
 for cls,name in [(datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')]:
  if isinstance(x,cls):return {'$type':name,'value':repr(x)if cls is float else str(x)}
 raise TypeError(str(type(x)))
def hashes(db):return {str(p):{'exists':p.exists(),**({'sha256':sha(p),'size':p.stat().st_size}if p.exists()else{})}for p in [Path(db),Path(db+'.wal')]}
def main():
 p=argparse.ArgumentParser();p.add_argument('--task',choices=RAW,required=True);p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output);out.mkdir(exist_ok=False,parents=True);db=os.environ.get('DUCKDB_PATH','/app/database/retail.duckdb');report={'kind':a.task,'database':db,'database_before':hashes(db),'collector':ref(__file__),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'raw':{}}
 try:
  with duckdb.connect(db,read_only=True)as c:
   report['duckdb_version']=duckdb.__version__
   for name,sql in RAW[a.task].items():
    st=c.extract_statements(sql);assert len(st)==1 and st[0].type==duckdb.StatementType.SELECT;schema=c.execute('DESCRIBE '+sql).fetchall();cur=c.execute(sql);cols=[v[0]for v in cur.description];rows=cur.fetchall();q=out/(name+'.jsonl');q.write_text(''.join(json.dumps([encode(v)for v in r])+'\n'for r in rows));report['raw'][name]={'source_sql':sql,'columns':cols,'sql_schema':schema,'rows':len(rows),'data':ref(q)}
 except Exception as e:report['capture_error']={'type':type(e).__name__,'message':str(e)}
 report['database_after']=hashes(db);report['database_and_wal_unchanged']=report['database_before']==report['database_after'];report['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();report['scope']='Initial SIF declared input projections only. Acquisition declaredupstreams must be rebuilt under taskworkflow before reference certification; this initial capture doesnot certify upstream semantic correctness.';(out/'capture.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'capture_error':report.get('capture_error'),'rows':{k:v['rows']for k,v in report['raw'].items()},'database_and_wal_unchanged':report['database_and_wal_unchanged']}))
if __name__=='__main__':main()
