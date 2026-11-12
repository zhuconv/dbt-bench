"""Read-only raw projections and physical output captures, with DB/WAL hashes."""
import argparse, datetime, decimal, hashlib, json, os
from pathlib import Path
import duckdb

RAW = {'ltv': {
 'customers':'SELECT customer_id FROM main.customers',
 'orders':'SELECT customer_id,order_id,grand_total,ordered_at,status,payment_status,test_order_flag,sample_order_flag,internal_order_flag FROM main.orders'},
 'gl': {
 'transactions':'SELECT transaction_id,transaction_number,account_id,period_id,transaction_date,debit_amount,credit_amount FROM FINANCE.GL_TRANSACTIONS',
 'accounts':'SELECT account_id,account_number,account_name,account_type,account_subtype FROM FINANCE.CHART_OF_ACCOUNTS',
 'periods':'SELECT period_id,period_name,fiscal_year,fiscal_quarter,fiscal_month,start_date,end_date,status FROM FINANCE.GL_PERIODS'}}
MODELS = {'ltv':['customer_ltv'],'gl':['account_balances','trial_balance','out_of_balance_entries','unusual_balance_accounts','period_summary']}
PROJECTS={'ltv':'/app/dbt_models_duckdb','gl':'/app/dbt_project'}
SCHEMAS={'ltv':'main','gl':'gl_analytics'}

def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def ref(p): return {'path':str(p),'sha256':sha(p)}

def hashes(db):
    return {str(p): {'exists':p.exists(), **({'sha256':sha(p),'size':p.stat().st_size} if p.exists() else {})} for p in [Path(db),Path(db+'.wal')]}

def encode(x):
    if x is None or type(x) in (str,int,bool): return x
    for cls,name in [(datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')]:
        if isinstance(x,cls): return {'$type':name,'value':repr(x) if cls is float else str(x)}
    raise TypeError(str(type(x)))

def capture(c, sql, dest):
    statements = c.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError('Only a single parsed SELECT is permitted')
    schema = c.execute('DESCRIBE '+sql).fetchall()
    cur = c.execute(sql); cols = [d[0] for d in cur.description]; rows = cur.fetchall()
    dest.write_text(''.join(json.dumps([encode(v) for v in r])+'\n' for r in rows))
    return ref(dest) | {'columns':cols,'rows':len(rows),'sql_schema':schema,'statement_types':[str(s.type) for s in statements]}

def inventory(project, out, target):
    paths = [project/'profiles.yml',project/'dbt_project.yml',project/'packages.yml',project/'package-lock.yml',project/target/'manifest.json',project/target/'run_results.json']
    paths += sorted(project.glob('models/**/*.sql'))
    paths += sorted(project.glob('models/**/*.yml'))
    paths += sorted(project.glob('macros/**/*.sql'))
    result = []
    for p in dict.fromkeys(paths):
        if not p.is_file(): result.append({'path':str(p),'exists':False}); continue
        # All model bytes are trusted-run provenance, not compared to a source whitelist.
        q = out/'project'/p.relative_to(project); q.parent.mkdir(parents=True,exist_ok=True); q.write_bytes(p.read_bytes())
        result.append({'path':str(p),'exists':True,'captured':ref(q),'bytes':q.stat().st_size})
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--task',choices=sorted(RAW),required=True);p.add_argument('--phase',choices=['initial','final'],required=True);p.add_argument('--output',required=True);p.add_argument('--target-path',default='audit_target');a=p.parse_args()
    assert a.target_path and not Path(a.target_path).is_absolute() and '..' not in Path(a.target_path).parts
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);project=Path(PROJECTS[a.task]);db=os.environ.get('DUCKDB_PATH','/app/database/retail.duckdb')
    r={'kind':a.task,'phase':a.phase,'project_root':str(project),'project_exists':project.exists(),'target_path':a.target_path,'database':db,'collector':ref(__file__),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'database_hashes_before':hashes(db),'environment':{k:os.environ.get(k) for k in ['DB_TYPE','DUCKDB_PATH','DBT_PROFILES_DIR','DBT_PROJECT_DIR_DUCKDB']}}
    try:
        assert os.environ.get('DB_TYPE')=='duckdb','Actual DB_TYPE must be duckdb'
        r['project_files']=inventory(project,out,a.target_path)
        with duckdb.connect(db,read_only=True) as c:
            r['duckdb_version']=duckdb.__version__
            relations=[sql.rsplit(' FROM ',1)[1] for sql in RAW[a.task].values()]
            relations += [SCHEMAS[a.task]+'.'+name for name in MODELS[a.task]]
            r['relation_metadata']=[];r['column_metadata']=[]
            for relation in dict.fromkeys(relations):
                schema,name=relation.split('.')
                r['relation_metadata']+=c.execute('SELECT table_catalog,table_schema,table_name,table_type FROM information_schema.tables WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?)',[schema,name]).fetchall()
                r['column_metadata']+=c.execute('SELECT table_schema,table_name,column_name,data_type,ordinal_position FROM information_schema.columns WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?) ORDER BY ordinal_position',[schema,name]).fetchall()
            r['raw']={key:capture(c,sql,out/(key+'.jsonl'))|{'source_sql':sql} for key,sql in RAW[a.task].items()}
            if a.phase=='final':
                r['physical_outputs']={}
                for name in MODELS[a.task]:
                    relation=SCHEMAS[a.task]+'.'+name
                    r['physical_outputs'][name]=capture(c,'SELECT * FROM '+relation,out/(name+'-physical.jsonl'))
    except Exception as exc:
        r['capture_error']={'type':type(exc).__name__,'message':str(exc)}
    finally:
        r['database_hashes_after']=hashes(db);r['database_and_wal_unchanged']=r['database_hashes_before']==r['database_hashes_after'];r['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();r['scope']='Only parsed single SELECT executed through read_only DuckDB. DB and WAL hashes before and after. Raw input projections and task project source/artifacts only, no complete DB export.'
        (out/'capture.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({'capture_error':r.get('capture_error'),'database_and_wal_unchanged':r['database_and_wal_unchanged']}))

if __name__=='__main__':main()
