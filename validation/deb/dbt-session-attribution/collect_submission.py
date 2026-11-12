"""Read explicit session inputs/output/source metadata in a diagnostic copy."""
from pathlib import Path
import hashlib,json,os,sys
import duckdb
from independent import SESSION_COLUMNS,SALES_COLUMNS,canonical,encoded


def main():
    phase=sys.argv[1];project=Path(sys.argv[2]);out=Path('/logs/agent/session-rebuild')/phase;out.mkdir(parents=True,exist_ok=False)
    db=Path(os.environ['DUCKDB_PATH']);inputs={};output=None;rows=[]
    with duckdb.connect(str(db),read_only=True) as con:
        for key,table,cols in [('sessions','stg_digital__web_sessions',SESSION_COLUMNS),('sales','stg_analytics__fact_sales',SALES_COLUMNS)]:
            cur=con.execute('select '+','.join(cols)+' from main.'+table);inputs[key]=[dict(zip(cols,r)) for r in cur.fetchall()]
        relations=con.execute("select table_schema,table_name,table_type from information_schema.tables where lower(table_name) in ('web_sessions','fact_sales','attribution_report') order by 1,2").fetchall()
        present=con.execute("select table_type from information_schema.tables where table_schema='main' and table_name='attribution_report'").fetchone()
        if present:
            cur=con.execute('select * from main.attribution_report');cols=[c[0].lower() for c in cur.description]
            output={'columns':cols,'rows':[dict(zip(cols,r)) for r in cur.fetchall()],'relation_type':present[0]}
    for name,value in [('inputs.json',canonical(json.loads(encoded(inputs)))),('output.json',output),('relations.json',relations)]:
        (out/name).write_text(encoded(value)+'\n')
    source_inventory={}
    if project.exists():
        for p in sorted(project.rglob('*')):
            rel=p.relative_to(project)
            if rel.parts[0] in {'logs','target','dbt_packages','.git'}:continue
            if p.is_symlink():raise ValueError('Unexpected submitted source symlink: '+str(rel))
            if p.is_file():source_inventory[str(rel)]=hashlib.sha256(p.read_bytes()).hexdigest()
        for name in ['manifest.json','run_results.json']:
            p=project/'target'/name
            if p.exists():(out/name).write_bytes(p.read_bytes())
    snapshot={'phase':phase,'project':str(project),'database':str(db),'python':sys.executable,
              'duckdb':duckdb.__version__,'read_only':True,'dbt_invoked':False,'source_inventory':source_inventory,
              'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}}
    (out/'snapshot.json').write_text(encoded(snapshot)+'\n')
    print(encoded({'phase':phase,'sources':len(source_inventory),'output_present':output is not None,'relations':relations}))


if __name__=='__main__':main()
