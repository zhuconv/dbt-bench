"""Read-only physical schema inventory; failures never imply missing objects."""
import argparse,hashlib,json
from pathlib import Path


def sha(path):
    p=Path(path)
    return hashlib.file_digest(p.open('rb'),'sha256').hexdigest() if p.is_file() else None


def probe(database, requirements):
    import duckdb
    if duckdb.__version__!='1.2.2':raise ValueError('Locked DuckDB1.2.2 required')
    before={x:sha(x) for x in (database,database+'.wal')}
    if before[database] is None:raise ValueError('Only a captured existing database may be probed')
    inventory={}
    with duckdb.connect(database,read_only=True) as c:
        for relation in requirements:
            schema,table=relation.split('.',1)
            found=c.execute('SELECT table_schema,table_name,table_type FROM information_schema.tables WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?)',[schema,table]).fetchall()
            if len(found)>1:raise ValueError('Ambiguous case-folded relation')
            columns=c.execute('SELECT column_name,data_type FROM information_schema.columns WHERE lower(table_schema)=lower(?) AND lower(table_name)=lower(?) ORDER BY ordinal_position',[schema,table]).fetchall()
            if not found and columns:raise ValueError('Contradictory inventory')
            inventory[relation]={'exists':bool(found),'relation_type':found[0][2] if found else None,'columns':[x[0] for x in columns],'sql_types':dict(columns)}
    after={x:sha(x) for x in before}
    if before!=after:raise ValueError('Read-only probe changed database')
    return {'inventory_complete':True,'inventory':inventory,'database':database,'hashes_before':before,'hashes_after':after,'database_and_wal_unchanged':True,'duckdb_version':duckdb.__version__,'model_requests':0}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    req=json.loads(a.request.read_text());result=probe(req['database'],req['requirements'])
    if a.output.exists():raise ValueError('Never overwrite observations')
    a.output.write_text(json.dumps(result,indent=2)+'\n')
